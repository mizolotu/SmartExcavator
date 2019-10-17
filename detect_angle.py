import pickle, json, os, requests, logging
import numpy as np
import tensorflow as tf

from sklearn.preprocessing import StandardScaler, MinMaxScaler
from sequence_generator import AngleDetector

from generate_levels import resample_cycle_points

if __name__ == '__main__':

    # switch off CUDA

    # os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

    # tf graphs and sessions

    angle_detection_graph = tf.Graph()
    angle_detection_session = tf.compat.v1.Session(graph=angle_detection_graph)

    # angle detection model file

    a_model_file = 'models/angle_detection/dense'

    # train data

    level_file = 'data/level_points.pkl'

    with open(level_file, 'rb') as f:
        level_points = pickle.load(f)
        key_points = pickle.load(f)
        key_stats = pickle.load(f)
        stats = pickle.load(f)

    X = []
    A = []
    X1 = []
    A1 = []
    for i in range(len(level_points)):
        print(i)
        px = level_points[i][0]
        pt = level_points[i][1]
        s = stats[i]
        dig_idx = s[2:4]
        emp_idx = s[4:6]
        dig_a = np.mean(px[dig_idx[0] : dig_idx[1], 0])
        emp_a = np.mean(px[emp_idx[0]: emp_idx[1], 0])
        l = emp_idx[1] - dig_idx[0] + 1
        for j in range(px.shape[0] - l):
            r = np.random.rand() * 100 - 50
            x = px[j:, :]
            t = pt[j:, :]
            points_resampled, time_step = resample_cycle_points({'x': x, 't': t}, time_step=0.25)
            n_points = points_resampled.shape[0]
            points_resampled[:, 0] += r
            dig_a_r = dig_a + r
            emp_a_r = emp_a + r
            X.append(points_resampled)
            A.append([dig_a_r, emp_a_r])

            dig_a_r = dig_a + r
            emp_a_r = emp_a + r
            a_min = np.min(points_resampled[:, 0])
            a_max = np.max(points_resampled[:, 0])
            a_step = 0.5
            a_window = 1
            for a in np.arange(a_min, a_max, a_step):
                idx = np.where((points_resampled[:, 0] > a - a_window) & (points_resampled[:, 0] < a + a_window))[0]
                if len(idx) > 0:
                    X1.append(points_resampled[idx, :])
                    if a > dig_a_r - a_window and a < dig_a_r + a_window:
                        A1.append([0, 1, 0])
                    elif a > emp_a_r - a_window and a < emp_a_r + a_window:
                        A1.append([0, 0, 1])
                    else:
                        A1.append([1, 0, 0])

    # standardize features

    x_min = np.array([-360.0, 3.9024162648733514, 13.252630737652677, 16.775050853637147])
    x_max = np.array([360.0, 812.0058600513476, 1011.7128949856826, 787.6024456729566])
    X_to_std = np.vstack([np.vstack(X), x_min, x_max])
    mm = MinMaxScaler().fit(X_to_std)

    n_steps = np.max([x.shape[0] for x in X])
    n_features = 4

    X_train = np.zeros((len(X), n_steps, n_features))
    Y_train = np.zeros((len(A), 2))

    n_steps1 = np.max([x.shape[0] for x in X1])
    n_features1= 3

    X_train1 = np.zeros((len(X1), n_steps1, n_features1))
    Y_train1 = np.zeros((len(A1), 3))

    print(X_train.shape, Y_train.shape)
    print(X_train1.shape, Y_train1.shape)

    for i in range(len(X)):

        n = X[i].shape[0]
        X_train[i, :n, :] = mm.transform(X[i])
        Y_train[i, 0] = (A[i][0] - x_min[0]) / (x_max[0] - x_min[0])
        Y_train[i, 1] = (A[i][1] - x_min[0]) / (x_max[0] - x_min[0])

    for i in range(len(X1)):

        n = X1[i].shape[0]
        X_train1[i, :n, :] = mm.transform(X1[i])[:, 1:]
        Y_train1[i, :] = A1[i]

    angler = AngleDetector(
        angle_detection_graph,
        angle_detection_session,
        n_steps1,
        n_features1,
        lr=0.0001,
        activation='softmax'
    )

    with angle_detection_graph.as_default():
        saver = tf.compat.v1.train.Saver()
        try:
            saver.restore(angle_detection_session, a_model_file)
        except Exception as e:
            print(e)
            angle_detection_session.run(tf.compat.v1.global_variables_initializer())
            angler.train(X_train1, Y_train1, epochs=10000, batch=10000)
            saver.save(angle_detection_session, a_model_file, write_meta_graph=False)



