#!/usr/bin/env python
# -*- coding: utf-8 -*-

""" SER: Speech Emotion Recognition example
"""

__version__ = '0.0.1'

import sys, time, logging, os, json
import numpy as np
np.set_printoptions(precision=20)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
# handler = logging.FileHandler(filename="log.txt")
handler.setFormatter(logging.Formatter('%(asctime)s - %(funcName)s - %(levelname)s - %(message)s'))
handler.setLevel(logging.DEBUG)
logger.addHandler(handler)

def pp(obj):
    import pprint
    pp = pprint.PrettyPrinter(indent=1, width=160)
    logger.info(pp.pformat(obj))


start_time = time.time()


import chainer
from chainer import cuda
import chainer.functions as F
import chainer.links as L
import matplotlib.pyplot as plt


def load_data(filename, labels={}, n_feature=384):
    X, y, z = [], [], []

    for i, line in enumerate(open(filename, 'rU')):
        # if i == 0:
        #     continue

        line = line.strip()
        if line == '':
            continue

        row = line.split('\t')
        if len(row) < 2:
            sys.stderr.write('invalid record: {}\n'.format(line))
            continue

        n_turn = int(row[0])
        for label in row[-(n_turn+1):]:
            if label not in labels:
                labels[label] = len(labels)

        X.append(chainer.Variable(xp.asarray(row[1:-(n_turn+1)], dtype=np.float32).reshape((n_turn, n_feature))))
        y.append(chainer.Variable(xp.asarray([labels[x] for x in row[-(n_turn+1):-1]], dtype=np.int32)))
        z.append(chainer.Variable(xp.asarray([labels[row[-1]]])))

    print('Loading dataset ... done.')
    sys.stdout.flush()

    return X, y, z, labels


# # Network definition
class SER(chainer.Chain):

    def __init__(self, n_input, n_layer_t, n_layer_c, n_units_t, n_units_c, n_output):
        super(SER, self).__init__()
        with self.init_scope():
            self.l1 = L.NStepBiLSTM(n_layer_t, n_input, n_units_t, 0.25)
            self.l2 = L.Linear(n_units_t * 2, n_output)
            self.em = L.EmbedID(n_output, n_input)
            self.l3 = L.NStepLSTM(n_layer_c, n_input, n_units_c, 0.25)
            self.l4 = L.Linear(n_units_c, n_output)

    def __call__(self, xs_t, ts_t, ts_c, alpha=.8, burnin=False):
        accum_loss_t = None
        accum_accuracy_t = None
        hx_t = None
        cx_t = None

        hx_t, cx_t, ys_t = self.l1(hx_t, cx_t, xs_t)

        xs_c = []

        for i in range(len(ts_t)):
            y, t = self.l2(ys_t[i]), ts_t[i]
            loss_t, accuracy_t = F.softmax_cross_entropy(y, t), F.accuracy(y, t)
            accum_loss_t = loss_t if accum_loss_t is None else accum_loss_t + loss_t
            accum_accuracy_t = accuracy_t if accum_accuracy_t is None else accum_accuracy_t + accuracy_t
            xs_c.append(chainer.Variable(xp.asarray([cuda.to_cpu(y.data[x]).argsort()[::-1][0] for x in range(y.data.shape[0])])))

        if burnin:
            return accum_loss_t, accum_accuracy_t, chainer.Variable(xp.zeros(1, dtype=np.float32))

        else:
            accum_loss_c = None
            accum_accuracy_c = None
            hx_c = None
            cx_c = None

            hx_c, cx_c, ys_c = self.l3(hx_c, cx_c, [self.em(x) for x in xs_c])

            for i in range(len(ts_c)):
                y, t = self.l4(ys_c[i][-1:,]), ts_c[i]
                loss_c, accuracy_c = F.softmax_cross_entropy(y, t), F.accuracy(y, t)
                accum_loss_c = loss_c if accum_loss_c is None else accum_loss_c + loss_c
                accum_accuracy_c = accuracy_c if accum_accuracy_c is None else accum_accuracy_c + accuracy_c

            accum_loss = accum_loss_t * alpha + accum_loss_c * (1 - alpha)

            return accum_loss, accum_accuracy_t, accum_accuracy_c

def batch_tuple(generator, batch_size):
    batch = []
    for line in generator:
        batch.append(line)
        if len(batch) == batch_size:
            yield tuple(list(x) for x in zip(*batch))
            batch = []
    if batch:
        yield tuple(list(x) for x in zip(*batch))


def main():
    global xp

    import argparse
    parser = argparse.ArgumentParser(description='SER example: NStep BiLSTM')
    parser.add_argument('--gpu', '-g', type=int, default=-1, help='GPU ID (negative value indicates CPU)')
    parser.add_argument('--layer_t', type=int, default=1,    help='Number of layes for turn')
    parser.add_argument('--layer_c', type=int, default=1,    help='Number of layes for call')
    parser.add_argument('--unit_t',  type=int, default=256,  help='Number of units for turn')
    parser.add_argument('--unit_c',  type=int, default=64,   help='Number of units for call')
    parser.add_argument('--dim',     type=int, default=384,  help='Number of dimensions')
    parser.add_argument('--burnin',  type=int, default=5,    help='Number of sweeps over the dataset to train')
    parser.add_argument('--batchsize', '-b', type=int, default=3,  help='Number of images in each mini-batch')
    parser.add_argument('--epoch',     '-e', type=int, default=20, help='Number of sweeps over the dataset to train')
    parser.add_argument('--train', default='train.tsv', type=str, help='training file (.txt)')
    parser.add_argument('--test',  default='test.tsv',  type=str, help='evaluating file (.txt)')
    parser.add_argument('--out', '-o', default='result', help='Directory to output the result')
    parser.add_argument('--noplot', dest='plot', action='store_true', help='Disable PlotReport extension')
    args = parser.parse_args()
    # args = parser.parse_args(args=[])
    print(json.dumps(args.__dict__, indent=2))

    if args.gpu >= 0:
        cuda.get_device_from_id(args.gpu).use()

    xp = cuda.cupy if args.gpu >= 0 else np
    xp.random.seed(123)

    model_dir = args.out
    if not os.path.exists(model_dir):
        os.mkdir(model_dir)

    # データの読み込み
    X_train, y_train, z_train, labels = load_data(args.train)
    X_test,  y_test,  z_test,  labels = load_data(args.test, labels=labels)

    n_class = len(labels)

    print('# train X: {}, y: {}, class: {}'.format(len(X_train), len(y_train), len(labels)))
    print('# eval  X: {}, y: {}, class: {}'.format(len(X_test), len(y_test), len(labels)))
    print('# class: {}'.format(n_class))
    sys.stdout.flush()

    model = SER(args.dim, args.layer_t, args.layer_c, args.unit_t, args.unit_c, n_class)
    if args.gpu >= 0:
        model.to_gpu(args.gpu)

    # 重み減衰
    decay = 0.0001

    # 勾配上限
    grad_clip = 3

    # 学習率の減衰
    lr_decay = 0.995

    # Setup optimizer (Optimizer の設定)
    optimizer = chainer.optimizers.Adam(alpha=0.001)
    optimizer.setup(model)
    # optimizer.add_hook(chainer.optimizer.GradientClipping(grad_clip))
    # optimizer.add_hook(chainer.optimizer.WeightDecay(decay))

    # プロット用に実行結果を保存する
    train_loss = []
    train_accuracy1 = []
    train_accuracy2 = []
    test_loss = []
    test_accuracy1 = []
    test_accuracy2 = []

    min_loss = float('inf')
    min_epoch = 0

    start_at = time.time()
    cur_at = start_at

    # Learning loop
    for epoch in range(1, args.epoch + 1):

        # training
        train_iter = batch_tuple([(s, t1, t2) for s, t1, t2 in zip(X_train, y_train, z_train)], args.batchsize)
        sum_train_loss = 0.
        sum_train_accuracy1 = 0.
        sum_train_accuracy2 = 0.
        K = 0

        for X, y, z in train_iter:

            # 勾配を初期化
            model.cleargrads()

            # 順伝播させて誤差と精度を算出
            if (epoch + 1) > args.burnin:
                loss, acc1, acc2 = model(X, y, z, burnin=False)
            else:
                loss, acc1, acc2 = model(X, y, z, burnin=True)

            sum_train_loss += float(loss.data)
            sum_train_accuracy1 += float(acc1.data)
            sum_train_accuracy2 += float(acc2.data)
            K += len(y)

            # 誤差逆伝播で勾配を計算
            loss.backward()
            optimizer.update()

        # 訓練データの誤差と,正解精度を表示
        mean_train_loss = sum_train_loss / K
        mean_train_accuracy1 = sum_train_accuracy1 / K
        mean_train_accuracy2 = sum_train_accuracy2 / K
        train_loss.append(mean_train_loss)
        train_accuracy1.append(mean_train_accuracy1)
        train_accuracy2.append(mean_train_accuracy2)
        now = time.time()
        train_throughput = now - cur_at
        cur_at = now

        # evaluation
        test_iter = batch_tuple([(s, t1, t2) for s, t1, t2 in zip(X_test, y_test, z_test)], 1)
        sum_test_loss = 0.
        sum_test_accuracy1 = 0.
        sum_test_accuracy2 = 0.
        K = 0

        with chainer.no_backprop_mode(), chainer.using_config('train', False):
            for X, y, z in test_iter:

                # 順伝播させて誤差と精度を算出
                loss, acc1, acc2 = model(X, y, z)
                sum_test_loss += float(loss.data)
                sum_test_accuracy1 += float(acc1.data)
                sum_test_accuracy2 += float(acc2.data)
                K += len(y)

        # テストデータでの誤差と正解精度を表示
        mean_test_loss = sum_test_loss / K
        mean_test_accuracy1 = sum_test_accuracy1 / K
        mean_test_accuracy2 = sum_test_accuracy2 / K
        test_loss.append(mean_test_loss)
        test_accuracy1.append(mean_test_accuracy1)
        test_accuracy2.append(mean_test_accuracy2)
        now = time.time()
        test_throughput = now - cur_at
        cur_at = now

        logger.info(''
                    '[{:>3d}] '
                    'T/loss={:.6f} '
                    'T/acc1={:.6f} '
                    'T/acc2={:.6f} '
                    'T/sec= {:.6f} '
                    'D/loss={:.6f} '
                    'D/acc1={:.6f} '
                    'D/acc2={:.6f} '
                    'D/sec= {:.6f} '
                    'lr={:.6f}'
                    ''.format(
            epoch,
            mean_train_loss,
            mean_train_accuracy1,
            mean_train_accuracy2,
            train_throughput,
            mean_test_loss,
            mean_test_accuracy1,
            mean_test_accuracy2,
            test_throughput,
            optimizer.alpha
        )
        )
        sys.stdout.flush()

        # model と optimizer を保存する
        if mean_test_loss < min_loss:
            min_loss = mean_test_loss
            min_epoch = epoch
            if args.gpu >= 0: model.to_cpu()
            chainer.serializers.save_npz(os.path.join(model_dir, 'early_stopped.model'), model)
            chainer.serializers.save_npz(os.path.join(model_dir, 'early_stopped.state'), optimizer)
            if args.gpu >= 0: model.to_gpu()

        # optimizer.alpha *= lr_decay

        # 精度と誤差をグラフ描画
        if not args.plot:
            ylim1 = [min(train_loss + test_loss), max(train_loss + test_loss)]
            ylim2 = [min(train_accuracy1 + test_accuracy2), max(train_accuracy1 + test_accuracy2)]

            # グラフ左
            plt.figure(figsize=(10, 10))

            plt.subplot(1, 2, 1)
            plt.ylim(ylim1)
            plt.plot(range(1, len(train_loss) + 1), train_loss, color='C1', marker='x')
            # plt.grid()
            plt.ylabel('loss')
            plt.legend(['train loss'], loc="lower left")
            plt.twinx()
            plt.ylim(ylim2)
            plt.plot(range(1, len(train_accuracy1) + 1), train_accuracy1, color='C0', marker='x')
            plt.plot(range(1, len(train_accuracy2) + 1), train_accuracy2, color='C2', marker='x')
            plt.yticks(np.arange(ylim2[0], ylim2[1], .1))
            plt.grid(True)
            # plt.ylabel('accuracy')
            plt.legend(['train turn', 'train call'], loc="upper right")
            plt.title('Loss and accuracy of train.')

            # グラフ右
            plt.subplot(1, 2, 2)
            plt.ylim(ylim1)
            plt.plot(range(1, len(test_loss) + 1), test_loss, color='C1', marker='x')
            # plt.grid()
            # plt.ylabel('loss')
            plt.legend(['dev loss'], loc="lower left")
            plt.twinx()
            plt.ylim(ylim2)
            plt.plot(range(1, len(test_accuracy1) + 1), test_accuracy1, color='C0', marker='x')
            plt.plot(range(1, len(test_accuracy2) + 1), test_accuracy2, color='C2', marker='x')
            plt.yticks(np.arange(ylim2[0], ylim2[1], .1))
            plt.grid(True)
            plt.ylabel('accuracy')
            plt.legend(['dev turn', 'dev call'], loc="upper right")
            plt.title('Loss and accuracy of dev.')

            plt.savefig('{}.png'.format(args.out))
            # plt.savefig('{}.png'.format(os.path.splitext(os.path.basename(__file__))[0]))
            # plt.show()

        cur_at = now

    # model と optimizer を保存する
    if args.gpu >= 0: model.to_cpu()
    chainer.serializers.save_npz(os.path.join(model_dir, 'final.model'), model)
    chainer.serializers.save_npz(os.path.join(model_dir, 'final.state'), optimizer)
    if args.gpu >= 0: model.to_gpu()

    logger.info('time spent: {:.6f} sec\n'.format(time.time() - start_time))


if __name__ == '__main__':
    main()
