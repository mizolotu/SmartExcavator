import tensorflow as tf

class TimePredictor(object):

    def __init__(self, graph, sess, n_features, n_hidden_units=32, n_dense_units=8, lr=0.00001):

        self.graph = graph
        self.sess = sess

        with self.graph.as_default():
            with self.sess.as_default():
                self.inputs = tf.compat.v1.placeholder(tf.float32, shape=[None, n_features])
                self.outputs = tf.compat.v1.placeholder(tf.float32, shape=[None, 1])
                hidden = tf.keras.layers.Dense(n_hidden_units, activation=tf.nn.relu)(self.inputs)
                dense = tf.keras.layers.Dense(n_dense_units, activation=tf.nn.relu)(hidden)
                self.prediction = tf.keras.layers.Dense(1)(dense)
                self.loss = tf.compat.v1.losses.mean_squared_error(labels=self.outputs, predictions=self.prediction)
                self.optimizer = tf.compat.v1.train.AdamOptimizer(learning_rate=lr).minimize(self.loss)

    def train(self, inputs, outputs, epochs=100000):
        for e in range(epochs):
            _, loss = self.sess.run([self.optimizer, self.loss], feed_dict={
                self.inputs: inputs,
                self.outputs: outputs
            })
            if e % int(epochs / 10) == 0:
                print('Loss at epoch {0}: {1}'.format(e, loss))

    def predict(self, inputs):
        return self.sess.run(self.prediction, feed_dict={
            self.inputs: inputs,
       })

class SequenceGenerator(object):

    def __init__(self, graph, sess, n_features, n_steps, n_dense_units=64, lr=0.0001):

        self.graph = graph
        self.sess = sess

        with self.graph.as_default():
            with self.sess.as_default():
                self.inputs = tf.compat.v1.placeholder(tf.float32, shape=[None, n_features])
                self.outputs = tf.compat.v1.placeholder(tf.float32, shape=[None, n_steps, n_features])
                dense = tf.keras.layers.Dense(n_dense_units, activation=tf.nn.relu)(self.inputs)
                prediction_vector = tf.keras.layers.Dense(n_steps * n_features, activation=tf.nn.sigmoid)(dense)
                self.prediction = tf.reshape(prediction_vector, shape=[tf.shape(self.inputs)[0], n_steps, n_features])
                self.loss = tf.compat.v1.losses.mean_squared_error(labels=self.outputs, predictions=self.prediction)
                self.optimizer = tf.compat.v1.train.AdamOptimizer(learning_rate=lr).minimize(self.loss)

    def train(self, inputs, outputs, epochs=10000):
        for e in range(epochs):
            _, loss = self.sess.run([self.optimizer, self.loss], feed_dict={
                self.inputs: inputs,
                self.outputs: outputs
            })
            if e % int(epochs / 10) == 0:
                print('Loss at epoch: {0}: {1}'.format(e, loss))

    def predict(self, inputs):
        return self.sess.run(self.prediction, feed_dict={
            self.inputs: inputs,
       })