import configparser
import os
import time
from argparse import ArgumentParser

import tensorflow as tf
from tqdm import tqdm

from utils.data_utils import DatasetVectorizer
from data.dataset import Dataset, DATASETS
from models.model_type import MODELS
from utils.batch_helper import BatchHelper
from utils.config_helpers import MainConfig
from utils.log_saver import LogSaver
from utils.model_saver import ModelSaver
from utils.other_utils import timer, evaluate_model, save_model_eval


def train(main_config, model_config, model_name, dataset_name):
    main_cfg = MainConfig(main_config)
    model = MODELS[model_name]
    dataset = DATASETS[dataset_name]()

    train_data = dataset.train_set_pairs()
    vectorizer = DatasetVectorizer(train_data, main_cfg.model_dir)

    dataset_helper = Dataset(vectorizer, dataset, main_cfg.batch_size)
    max_sentence_len = vectorizer.max_sentence_len
    vocabulary_size = vectorizer.vocabulary_size

    # print(snli_dataset)

    test_sen1, test_sen2 = dataset_helper.test_instances()
    test_labels = dataset_helper.test_labels()
    test_labels = test_labels.reshape(-1, 1)
    num_batches = dataset_helper.num_batches

    model = model(max_sentence_len, vocabulary_size, main_config, model_config)

    model_saver = ModelSaver(main_cfg.model_dir, model_name, main_cfg.checkpoints_to_keep)

    config = tf.ConfigProto(allow_soft_placement=True, log_device_placement=main_cfg.log_device_placement)

    with tf.Session(config=config) as session:
        global_step = 0
        init = tf.global_variables_initializer()
        session.run(init)
        log_saver = LogSaver(main_cfg.logs_path, model_name, dataset_name, session.graph)
        metrics = {'acc': 0.0}
        test_acc_per_epoch = []
        time_per_epoch = []
        for epoch in tqdm(range(main_cfg.num_epochs), desc='Epochs'):
            start_time = time.time()

            train_sen1, train_sen2 = dataset_helper.train_instances(shuffle=True)
            train_labels = dataset_helper.train_labels()

            train_batch_helper = BatchHelper(train_sen1, train_sen2, train_labels, main_cfg.batch_size)

            # small eval set for measuring train accuracy
            val_sen1, val_sen2, val_labels = dataset_helper.dev_instances()
            val_labels = val_labels.reshape(-1, 1)
            tqdm_iter = tqdm(range(num_batches), total=num_batches, desc="Batches", leave=False, postfix=metrics)

            for batch in tqdm_iter:
                global_step += 1
                x1_batch, x2_batch, y_batch = train_batch_helper.next(batch)
                feed_dict = {model.x1: x1_batch, model.x2: x2_batch, model.labels: y_batch}
                loss, _ = session.run([model.loss, model.opt], feed_dict=feed_dict)

                if batch % main_cfg.eval_every == 0:
                    feed_dict = {model.x1: val_sen1, model.x2: val_sen2, model.labels: val_labels}
                    train_accuracy, train_summary, loss = session.run([model.accuracy, model.summary_op, model.loss],
                                                                      feed_dict=feed_dict)
                    log_saver.log_train(train_summary, global_step)

                    feed_dict = {model.x1: test_sen1,
                                 model.x2: test_sen2,
                                 model.labels: test_labels}
                    test_accuracy, test_summary = session.run([model.accuracy, model.summary_op], feed_dict=feed_dict)
                    log_saver.log_test(test_summary, global_step)

                    tqdm_iter.set_postfix(
                        train_test_acc='{:.2f}|{:.2f}'.format(float(train_accuracy), float(test_accuracy)),
                        loss='{:.2f}'.format(float(loss)),
                        epoch=epoch)

                if global_step % main_cfg.save_every == 0:
                    model_saver.save(session, global_step=global_step)

            test_accuracy = evaluate_model(model, session, test_sen1, test_sen2, test_labels)
            test_acc_per_epoch.append(test_accuracy)

            end_time = time.time()
            total_time = timer(start_time, end_time)
            time_per_epoch.append(total_time)

            model_saver.save(session, global_step=global_step)

        save_model_eval('{}/{}'.format(main_cfg.model_dir, model_name), sum(test_acc_per_epoch)/len(test_acc_per_epoch),
                        test_acc_per_epoch[-1],
                        time_per_epoch[-1])


def predict(main_config, model_config, model):

    model_name = '{}_{}'.format(model,
                                main_config['PARAMS']['embedding_size'])
    model = MODELS[model_name]
    model_dir = str(main_config['DATA']['model_dir'])

    vectorizer = DatasetVectorizer(model_dir)

    max_doc_len = vectorizer.max_sentence_len
    vocabulary_size = vectorizer.vocabulary_size

    model = model(max_doc_len, vocabulary_size, main_config, model_config)

    with tf.Session() as session:
        saver = tf.train.Saver()
        last_checkpoint = tf.train.latest_checkpoint('{}/{}/model'.format(model_dir, model_name))
        saver.restore(session, last_checkpoint)
        while True:
            x1 = input('First sentence:')
            x2 = input('Second sentence:')
            x1_sen = vectorizer.vectorize(x1)
            x2_sen = vectorizer.vectorize(x2)

            feed_dict = {model.x1: x1_sen, model.x2: x2_sen}
            prediction = session.run([model.temp_sim], feed_dict=feed_dict)
            print(prediction)


def main():
    parser = ArgumentParser()

    parser.add_argument('mode',
                        choices=['train', 'predict'],
                        help='pipeline mode')

    parser.add_argument('model',
                        choices=['rnn', 'cnn', 'multihead'],
                        help='model to be used')

    parser.add_argument('dataset',
                        choices=['QQP', 'SNLI'],
                        help='dataset to be used')

    parser.add_argument('--gpu',
                        default='0',
                        help='index of GPU to be used (default: %(default))')

    args = parser.parse_args()

    os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu

    main_config = configparser.ConfigParser()
    main_config.read('config/main.ini')

    model_config = configparser.ConfigParser()
    model_config.read('config/model/{}.ini'.format(args.model))

    mode = args.mode

    if 'train' in mode:
        train(main_config, model_config, args.model, args.dataset)
    else:
        predict(main_config, model_config, args.model)


if __name__ == '__main__':
    main()
