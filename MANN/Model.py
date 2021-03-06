import tensorflow as tf
import numpy as np

from .Utils.init import weight_and_bias_init, shared_glorot_uniform, shared_one_hot
from .Utils.similarities import cosine_similarity
from .Utils.tf_utils import shared_float32


def memory_augmented_neural_network(input_var, target_var, \
                                    batch_size=16, nb_class=5, memory_shape=(128, 40), \
                                    controller_size=200, input_size=20 * 20, nb_reads=4):
    ## input_var has dimensions (batch_size, time, 	input_dim)
    ## target_var has dimensions (batch_size, time) (label indices)

    M_0 = shared_float32(1e-6 * np.ones((batch_size,) + memory_shape), name='memory')
    c_0 = shared_float32(np.zeros((batch_size, controller_size)), name='memory_cell_state')
    h_0 = shared_float32(np.zeros((batch_size, controller_size)), name='hidden_state')
    r_0 = shared_float32(np.zeros((batch_size, nb_reads * memory_shape[1])), name='read_vector')
    wr_0 = shared_one_hot((batch_size, nb_reads, memory_shape[0]), name='wr')
    wu_0 = shared_one_hot((batch_size, memory_shape[0]), name='wu')

    with tf.variable_scope("Weights"):
        print "Init : ",tf.VariableScope.name
        high = tf.constant(1e-3)
        W_key = tf.get_variable('W_key', shape=(nb_reads, controller_size, memory_shape[1]),initializer=tf.random_uniform_initializer(-1*high, high))
        b_key = tf.get_variable('b_key', shape=(nb_reads, memory_shape[1]),initializer=tf.constant_initializer(0))
        W_add = tf.get_variable('W_add', shape=(nb_reads, controller_size, memory_shape[1]),initializer=tf.random_uniform_initializer(-1*high, high))
        b_add = tf.get_variable('b_add', shape=(nb_reads, memory_shape[1]),initializer=tf.constant_initializer(0))
        W_sigma = tf.get_variable('W_sigma', shape=(nb_reads, controller_size, 1),initializer=tf.random_uniform_initializer(-1*high, high))
        b_sigma = tf.get_variable('b_sigma', shape=(nb_reads, 1),initializer=tf.constant_initializer(0))
        W_xh = tf.get_variable('W_xh', shape=(input_size + nb_class, 4*controller_size),initializer=tf.random_uniform_initializer(-1*high, high))
        b_h = tf.get_variable('b_xh', shape=(4*controller_size),initializer=tf.constant_initializer(0))
        W_o = tf.get_variable('W_o', shape=(controller_size + nb_reads * memory_shape[1], nb_class),initializer=tf.random_uniform_initializer(-1*high, high))
        b_o = tf.get_variable('b_o', shape=(nb_class),initializer=tf.constant_initializer(0))
        W_rh = tf.get_variable('W_rh', shape=(nb_reads * memory_shape[1], 4 * controller_size),initializer=tf.random_uniform_initializer(-1*high, high))
        W_hh = tf.get_variable('W_hh', shape=(controller_size, 4*controller_size),initializer=tf.random_uniform_initializer(-1*high, high))
        gamma = tf.get_variable('gamma', shape=[1], initializer=tf.constant_initializer(0.95))

    """W_key, b_key = weight_and_bias_init((controller_size, memory_shape[1]), name='key', n=nb_reads)
        W_add, b_add = weight_and_bias_init((controller_size, memory_shape[1]), name='add', n=nb_reads)
        W_sigma, b_sigma = weight_and_bias_init((controller_size, 1), name='sigma', n=nb_reads)

        W_xh, b_h = weight_and_bias_init((input_size + nb_class, 4 * controller_size),name='xh')
        W_rh = shared_glorot_uniform((nb_reads * memory_shape[1], 4 * controller_size), name='W_rh')
        W_hh = shared_glorot_uniform((controller_size, 4*controller_size),name='W_hh')
        W_o, b_o = weight_and_bias_init((controller_size + nb_reads * memory_shape[1], nb_class),name='o')
        gamma = tf.constant(0.95, name='gamma')"""

    def slice_equally(x, size, nb_slice):
        # type: (object, object, object) -> object
        return [x[:,n*size:(n+1)*size] for n in range(nb_slice)]

    def update_tensor(V, dim2, val):  # Update tensor V, with index(:,dim2[:]) by val[:]
        val = tf.cast(val, V.dtype)
        print V.get_shape().as_list(), dim2.get_shape().as_list(), val.get_shape().as_list()
        with tf.variable_scope('temp', reuse=None):
            temp = tf.get_variable(name="Black2", shape=V.get_shape().as_list()[1],
                                   initializer=tf.constant_initializer(0, dtype=V.dtype))

        def body((v, d2, chg),_):
            d2_int = tf.cast(d2, tf.int32)
            with tf.variable_scope('temp', reuse=True):
                temp = tf.get_variable(name="Black2", shape=v.get_shape().as_list(),
                                       initializer=tf.constant_initializer(0, dtype=V.dtype))
            temp[:].assign(v)
            temp[d2_int].assign(chg)
            return temp, d2, chg
        Z, _, _ = tf.scan(body, elems=(V, dim2, val), name="Scan_Update")
        with tf.control_dependencies([Z]):
            return Z

    def update_tensor2(V, dim2, val):  # Update tensor V, with index(:,dim2[:]) by val[:]
        val = tf.cast(val, V.dtype)
        print V.get_shape().as_list(), dim2.get_shape().as_list(), val.get_shape().as_list()
        print 'Metric Third brrrrrrrrrrr=======================**********>>>>'
        with tf.variable_scope('temp2', reuse=None):
            temp = tf.get_variable(name="Black1", shape=V.get_shape().as_list()[1:],
                                   initializer=tf.constant_initializer(0, dtype=V.dtype))

        def body((v, d2, chg),_):
            with tf.variable_scope('temp2', reuse=True):
                temp = tf.get_variable(name="Black1", shape=v.get_shape().as_list(),
                                       initializer=tf.constant_initializer(0, dtype=v.dtype))
            d2_int = tf.cast(d2, tf.int32, name="d2_new")

            temp[:].assign(v)
            temp[d2_int].assign(chg)
            return temp, d2, chg
        Z, _, _ = tf.scan(body, elems=(V, dim2, val), name="Metric_Update_Scan")
        return Z


    def step((M_tm1, c_tm1, h_tm1, r_tm1, wr_tm1, wu_tm1),(x_t)):

        with tf.variable_scope("Weights", reuse=True):
            high = tf.constant(1e-3)
            W_key = tf.get_variable('W_key', shape=(nb_reads, controller_size, memory_shape[1]),
                                    initializer=tf.random_uniform_initializer(-1 * high, high))
            b_key = tf.get_variable('b_key', shape=(nb_reads, memory_shape[1]), initializer=tf.constant_initializer(0))
            W_add = tf.get_variable('W_add', shape=(nb_reads, controller_size, memory_shape[1]),
                                    initializer=tf.random_uniform_initializer(-1 * high, high))
            b_add = tf.get_variable('b_add', shape=(nb_reads, memory_shape[1]), initializer=tf.constant_initializer(0))
            W_sigma = tf.get_variable('W_sigma', shape=(nb_reads, controller_size, 1),
                                      initializer=tf.random_uniform_initializer(-1 * high, high))
            b_sigma = tf.get_variable('b_sigma', shape=(nb_reads, 1), initializer=tf.constant_initializer(0))
            W_xh = tf.get_variable('W_xh', shape=(input_size + nb_class, 4 * controller_size),
                                   initializer=tf.random_uniform_initializer(-1 * high, high))
            b_h = tf.get_variable('b_xh', shape=(4 * controller_size), initializer=tf.constant_initializer(0))
            W_o = tf.get_variable('W_o', shape=(controller_size + nb_reads * memory_shape[1], nb_class),
                                  initializer=tf.random_uniform_initializer(-1 * high, high))
            b_o = tf.get_variable('b_o', shape=(nb_class), initializer=tf.constant_initializer(0))
            W_rh = tf.get_variable('W_rh', shape=(nb_reads * memory_shape[1], 4 * controller_size),
                                   initializer=tf.random_uniform_initializer(-1 * high, high))
            W_hh = tf.get_variable('W_hh', shape=(controller_size, 4 * controller_size),
                                   initializer=tf.random_uniform_initializer(-1 * high, high))
            gamma = tf.get_variable('gamma', shape=[1], initializer=tf.constant_initializer(0.95))

        #x_t = tf.transpose(X_t, perm=[1, 0, 2])[ix]
        preactivations = tf.matmul(x_t,W_xh) + tf.matmul(r_tm1,W_rh) + tf.matmul(h_tm1,W_hh) + b_h
        gf_, gi_, go_, u_ = slice_equally(preactivations, controller_size, 4)
        gf = tf.sigmoid(gf_)
        gi = tf.sigmoid(gi_)
        go = tf.sigmoid(go_)
        u = tf.sigmoid(u_)

        c_t = gf*c_tm1 + gi*u
        h_t = go * tf.tanh(c_t)  #(batch_size, controller_size)

        h_t_W_key = tf.matmul(h_t, tf.reshape(W_key, shape=(controller_size,-1)))
        k_t = tf.tanh(tf.reshape(h_t_W_key, shape=(batch_size, nb_reads, memory_shape[1])) + b_key)  #(batch_size, nb_reads, memory_shape[1])
        h_t_W_add = tf.matmul(h_t, tf.reshape(W_add, shape=(controller_size, -1)))
        a_t = tf.tanh(tf.reshape(h_t_W_add, shape=(batch_size, nb_reads, memory_shape[1]))  + b_add)
        h_t_W_sigma = tf.matmul(h_t, tf.reshape(W_sigma, shape=(controller_size, -1)))
        sigma_t = tf.sigmoid(tf.reshape(h_t_W_sigma, shape=(batch_size, nb_reads,1)) + b_sigma)  #(batch_size, nb_reads, 1)

        _,temp_indices = tf.nn.top_k(wu_tm1, memory_shape[0])
        wlu_tm1 = tf.slice(temp_indices, [0,0], [batch_size,nb_reads])    #(batch_size, nb_reads)

        sigma_t_wr_tm_1 = tf.tile(sigma_t, tf.pack([1, 1, wr_tm1.get_shape().as_list()[2]]))
        ww_t = tf.reshape(tf.mul(sigma_t, wr_tm1), (batch_size*nb_reads, memory_shape[0]))    #(batch_size*nb_reads, memory_shape[0])
        ww_t = update_tensor(ww_t, tf.reshape(wlu_tm1,[-1]),1.0 - tf.reshape(sigma_t,shape=[-1]))
        ww_t = tf.reshape(ww_t,(batch_size, nb_reads, memory_shape[0]))

        M_t = update_tensor2(M_tm1, wlu_tm1[:,0], tf.constant(0., shape=[batch_size]))
        M_t = tf.add(M_t, tf.batch_matmul(tf.transpose(ww_t, perm=[0,2,1]   ), a_t))   #(batch_size, memory_size[0], memory_size[1])
        K_t = cosine_similarity(k_t, M_t)

        wr_t = tf.nn.softmax(tf.reshape(K_t, (batch_size*nb_reads, memory_shape[0])))
        wr_t = tf.reshape(wr_t, (batch_size, nb_reads, memory_shape[0]))    #(batch_size, nb_reads, memory_size[0])

        wu_t = gamma * wu_tm1 + tf.reduce_sum(wr_t, axis=1) + tf.reduce_sum(ww_t, axis=1) #(batch_size, memory_size[0])

        r_t = tf.reshape(tf.batch_matmul(wr_t, M_t),[batch_size,-1])

        return [M_t, c_t, h_t, r_t, wr_t, wu_t]

    #Model Part:
    sequence_length_var = target_var.get_shape().as_list()[1]   #length of the input
    output_shape_var = (tf.mul(batch_size, sequence_length_var), nb_class)     #(batch_size*sequence_length_vat,nb_class)

    # Input concat with time offset
    one_hot_target_flattened = tf.one_hot(tf.reshape(target_var,[-1]), depth=nb_class)
    one_hot_target = tf.reshape(one_hot_target_flattened, (batch_size, sequence_length_var, nb_class))    #(batch_size, sequence_var_length, nb_class)
    offset_target_var = tf.concat_v2([tf.zeros_like(tf.expand_dims(one_hot_target[:,0],1)),one_hot_target[:,:-1]],axis=1)   #(batch_size, sequence_var_length, nb_class)
    l_input_var = tf.concat_v2([input_var,offset_target_var],axis=2)    #(batch_size, sequence_var_length, input_size+nb_class)

    #ix = tf.variable(0,dtype=tf.int32)
    #cond = lambda M_0, c_0, h_0, r_0, wr_0, wu_0, ix: ix < sequence_length_var
    l_ntm_var = tf.scan(step, elems=tf.transpose(l_input_var, perm=[1,0,2]),initializer=[M_0, c_0, h_0, r_0, wr_0, wu_0], name="Scan_MANN_Last")   #Set of all above parameters, as list
    l_ntm_output_var = tf.transpose(tf.concat_v2(l_ntm_var[2:4], axis=2), perm=[1, 0, 2])     #h_t & r_t, size=(batch_size, sequence_var_length, controller_size+nb_reads*memory_size[1])

    l_input_var_W_o = tf.matmul(tf.reshape(l_ntm_output_var, shape=(batch_size*sequence_length_var,-1)), W_o)
    output_var_preactivation = tf.add(tf.reshape(l_input_var_W_o, (batch_size, sequence_length_var,nb_class)), b_o)
    output_var_flatten = tf.nn.softmax(tf.reshape(output_var_preactivation, output_shape_var))
    output_var = tf.reshape(output_var_flatten, output_var_preactivation.get_shape().as_list())

    #Parameters
    params = [W_key, b_key, W_add, b_add, W_sigma, b_sigma, W_xh, W_rh, W_hh, b_h, W_o, b_o]

    return output_var, output_var_flatten, params