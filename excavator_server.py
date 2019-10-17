import pickle, json, os, requests, loggingimport numpy as npimport tensorflow as tffrom time import time, sleepfrom sklearn.preprocessing import StandardScaler, MinMaxScalerfrom flask import Flask, request, jsonifyfrom threading import Threadfrom itertools import productfrom sequence_generator import TimePredictor, SequenceGeneratorfrom a2c import Actor, Criticfrom ppo import Actor as Actor_ppofrom ppo import Critic as Critic_ppofrom replay_buffer import ReplayBufferfrom matplotlib import pyplot as pp# switch off CUDAos.environ["CUDA_VISIBLE_DEVICES"]="-1"# init Flask appapp = Flask(__name__)log = logging.getLogger('werkzeug')log.disabled = True# tf graphs and sessionssequence_generation_graph = tf.Graph()sequence_generation_session = tf.compat.v1.Session(graph=sequence_generation_graph)mover_graph = tf.Graph()mover_session = tf.compat.v1.Session(graph=mover_graph)planner_graph = tf.Graph()planner_session = tf.compat.v1.Session(graph=planner_graph)# other global variablesmode = 'USER'pause = False# excavator API@app.route('/mode', methods=['GET', 'POST'])def get_mode():    # global variables    global mode, p_count, p_tic, p_episode_count, p_action, p_hole, m_tic, m_target_count, m_count, m_episode_count, m_action, m_state, m_score, dig_angle    # GET request    if request.method == 'GET':        # GET request when under user control        if mode == 'USER':            current_dig_angle = analyze_user_input()        # GET request when under AI control        else:            current_dig_angle = dig_angle    # POST request    elif request.method == 'POST':        data = request.data.decode('utf-8')        jdata = json.loads(data)        new_mode = jdata['mode']        # switching to the user control        if new_mode == 'USER':            current_dig_angle = None        # switching to the AI control        elif new_mode.startswith('AI'):            # AI training            if new_mode.endswith('TRAIN'):                dig_angle = generate_user_input(p_episode_count)            # AI testing            elif new_mode.endswith('TEST'):                dig_angle = analyze_user_input(fname='user_input/user_input.txt') # CHANGE THIS TO THE FILE WITH REAL USER INPUT!            # if dig angle is set            if dig_angle is not None:                current_dig_angle = dig_angle                # nulify states and actions                p_tic = time()                p_count = 0                p_episode_count = 0                p_state = None                p_action = None                p_next_state = None                p_next_action = None                p_hole = np.zeros(p_action_dims)                if mode.endswith('TEST'):                    try:                        score = jdata['score']                    except:                        score = 0.0                    p_hole[0] = score                m_tic = time()                m_target_count = 0                m_count = 0                m_episode_count = 0                m_state = None                m_action = None                m_score = 0            # otherwise            else:                # clean previous user input                open('user_input/user_input.txt','w').close()                # do not do anything                new_mode = 'USER'                current_dig_angle = None        else:            # unknown mode            current_dig_angle = None        # update the mode        mode = new_mode    data_to_return = {'mode': mode, 'dig_angle': current_dig_angle}    return jsonify(data_to_return)def moving_average(x, step=2, window=2):    seq = []    n = x.shape[0]    for i in np.arange(0, n, step):        idx = np.arange(np.maximum(0, i - window), np.minimum(n - 1, i + window + 1))        seq.append(np.mean(x[idx, :], axis=0))    return np.vstack(seq)def generate_sequence(model, rx_start, n_points, stretch=None):    a_start = rx_start[0]    x_start = rx_start[1:]    latent = mm.transform(ss.transform(x_start.reshape(1, n_features)))    next_steps = model.predict(latent)    y = ss.inverse_transform(mm.inverse_transform(next_steps.reshape(n_points, n_features)))    seq = np.hstack([a_start * np.ones((n_points, 1)), y])    seq = moving_average(seq)    idx_s = [0, 0, np.argmin(seq[:, 3]), 0]    if stretch is not None:        for i in np.arange(1,action_dim):            mu = seq[idx_s[i], i]            si = np.std(seq[:, i])            z = (seq[:, i] - mu) / (si + 1e-6)            idx = np.where(z <= 0)[0]            seq[idx, i] = mu + (1 + stretch[i]) * si * z[idx]    #seq = moving_average(seq, step=1, window=2)    return seqdef interp_x_by_r(seq_start, seq_end, n_steps):    r = np.hstack([seq_start[0], seq_end[0]])    step = (seq_end[0] - seq_start[0]) / (n_steps - 1)    r_ = seq_start[0] + step * np.arange(n_steps)    seq = np.zeros((n_steps, len(seq_start)))    seq[:, 0] = r_    for i in range(1, len(seq_start)):        x = np.hstack([seq_start[i], seq_end[i]])        seq[:, i] = np.interp(r_, r, x)    return seqdef build_trajectory(last_point, dig, emp):    trajectory = np.vstack([        last_point,        dig,        emp    ])    return trajectorydef predict_time(seq):    deltas = np.abs(seq[1:, :] - seq[:-1, :])    deltas_std = ss_D.transform(deltas)    t = timer.predict(deltas_std)    t = np.clip(t, np.min(TY), np.max(TY))    return tdef test_digger():    random_last_step_idx = np.random.randint(0, len(DX))    last_step = DX[random_last_step_idx, :].reshape(1, -1)    x = ss.inverse_transform(mm.inverse_transform(last_step))    x = np.hstack([90, x.reshape(n_features)])    y0 = generate_sequence(digger, x, n_points=n_steps, stretch=None)    yd1 = generate_sequence(digger, x, n_points=n_steps, stretch = np.array([0, 10.0 / p_time_dim, 10.0 / p_time_dim, 10.0 / p_time_dim]))    ys1 = generate_sequence(digger, x, n_points=n_steps, stretch=np.array([0, 5.0 / p_time_dim, 5.0 / p_time_dim, 5.0 / p_time_dim]))    pp.plot(y0, '--')    pp.plot(yd1)    pp.show()    pp.plot(y0, '--')    pp.plot(ys1)    pp.show()@app.route('/trajectory')def get_trajectory():    # global variables    global p_tic, p_state, p_action, p_next_state, p_next_action, p_hole, p_count, p_episode_count, dig_angle    # request and response data    data = request.data.decode('utf-8')    jdata = json.loads(data)    x = jdata['x']    last_step = np.array(x)  # last position    score = jdata['score']   # ground taken    data_to_send = {'y': [], 't': []}    # plan the next action    print('Trajectory completed in {0} seconds\n'.format(time() - p_tic))    p_tic = time()    if p_action is not None:        hole_idx = np.unravel_index(p_action.argmax(), p_action_dims)        p_hole[hole_idx] = score    p_next_state = p_hole.reshape(1, 1, p_action_dim)    # predict next action    p_next_action = p_actor.predict(p_next_state)    idx = np.unravel_index(p_next_action.argmax(), p_action_dims)    angle = dig_angle + p_action_vals[0][idx[0]] * p_angle_gain    stretch = np.zeros(action_dim)    for i in np.arange(1, action_dim):        stretch[i] = p_action_vals[1][idx[1]] * p_stretch_gain / p_time_dim    print('Planner stats:')    print('Score: {0}\nAngle: {1} {2} {3}\nStretch: {4}\n'.format(        score,        dig_angle,        '+' if p_action_vals[0][idx[0]] * p_angle_gain >= 0 else '-',        np.abs(p_action_vals[0][idx[0]] * p_angle_gain),        stretch[1])    )    dig_first_step = np.hstack([angle, x[1:]])    dig_last_step = np.hstack([angle, dig_end_point[1:]])    dig = generate_sequence(digger, dig_first_step, n_points=n_steps, stretch=stretch)    dig[-1, :] = dig_last_step    emp_first_step = np.hstack([emp_mean_angle, emp_start_point[1:]])    emp_last_step = np.hstack([emp_mean_angle, emp_end_point[1:]])    emp = generate_sequence(emptier, emp_first_step, n_points=n_steps)    emp[-1, :] = emp_last_step    full_trajectory = build_trajectory(last_step, dig, emp)    full_trajectory = clip_seq(full_trajectory, rx_min, rx_max)    trajectory_time = predict_time(full_trajectory)    trajectory_time = trajectory_time.reshape(trajectory_time.shape[0])    done = False    p_count += 1    if p_action is not None:        if p_count == p_time_dim:            done = True        if p_count >= 1 and not p_replay_buffer_lock:            p_replay_buffer.add(                np.reshape(p_state, (1, p_action_dim)),                np.reshape(p_action, (p_action_dim,)),                [score],                done,                np.reshape(p_next_state, (1, p_action_dim))            )    # check finished or not    if done:        # learn to plan        batch = p_replay_buffer.sample_batch(p_count, rnd=False)        learning_thread = Thread(target=learn_plans, args=(batch,))        learning_thread.setDaemon(True)        learning_thread.start()        p_replay_buffer.clear()        # nulify state        p_hole = np.zeros(p_action_dims)        p_state = None        p_action = None        p_count = 0        # increment episode count        p_episode_count += 1        dig_angle = generate_user_input(p_episode_count)    else:        p_state = np.array(p_next_state)        p_action = np.array(p_next_action)    data_to_send['y'] = full_trajectory[1:, :].tolist()    data_to_send['t'] = trajectory_time.tolist()    return jsonify(data_to_send)@app.route('/controls')def get_controls():    # global variables    global m_tic, m_state, m_action, m_count, m_episode_count, m_score, m_target_count    # request and response data    data = request.data.decode('utf-8')    jdata = json.loads(data)    deltas = np.array(jdata['deltas'])    deltas_to_next = np.mean(np.array(jdata['deltas_to_next']), axis=0)    delta_start = np.array(jdata['delta_start'])    delta_end = np.array(jdata['delta_end'])    in_target = jdata['in_target']    time_passed = jdata['time']    time_limit = jdata['time_limit']    done = jdata['done']    data_to_send = {}    # calculate score    tol = 1e-8    error_thr = 3    score = time_limit - time_passed    if np.all(np.abs(delta_end) <= error_thr):        if m_action is not None:            m_target_count += 1    if m_action is not None:        m_score += score    # predict next action    m_next_state = np.mean(ss_d.transform(deltas), axis=0).reshape(m_time_dim, state_dim)    m_next_action = m_actor.predict(np.reshape(m_next_state, (1, m_time_dim, state_dim)))    item = m_next_action[0]    gains = np.array([100, 100, 100, 100, 0.5, 0.01, 0.01, 0.05, 5, item[0], item[1], item[2]])    act_values = gains.reshape(pid_dim, action_dim)    data_to_send['controls'] = act_values.tolist()    # check if action is not none and there is need for a action, i.e. deltas are greater than threshold    m_count += 1    if m_action is not None:        if m_count == m_count_max:            done = True        if m_count > 1:            m_replay_buffer.add(                m_state,                np.reshape(m_action, (m_action_dim, )),                np.array([score]),                done,                m_next_state            )    else:        data_to_send['controls'] = [            [100, 0.2, 0.2, 0.2],            [1, 0.0, 0.0, 0.0],            [100, 0.0, 0.0, 0.0]        ]    # check finished or not    if done:        # print stats        print('Mover stats:')        print('Score: {0}\n'.format(m_score))        # learn to move        try:            batch = m_replay_buffer.sample_batch(m_count, rnd=False)            learning_thread = Thread(target=learn_moves, args=(batch, m_epochs))            learning_thread.setDaemon(True)            learning_thread.start()        except Exception as e:            print(e)        m_replay_buffer.clear()        # nulify state        m_state = None        m_action = None        m_score = 0        m_count = 0        m_target_count = 0        m_episode_count += 1        data_to_send['controls'] = [            [100, 0.2, 0.2, 0.2],            [1, 0.0, 0.0, 0.0],            [100, 0.0, 0.0, 0.0]        ]    else:        m_state = np.array(m_next_state)        m_action = np.array(m_next_action)    return jsonify(data_to_send)# Learn to movedef learn_moves(batch, n_epochs=4):    s_batch, a_batch, r_batch, t_batch, s2_batch, i_batch, w_batch = batch    v_batch = m_critic.predict(s_batch)    adv_batch = np.zeros_like(v_batch)    nsteps = s_batch.shape[0]    for i in reversed(range(nsteps)):        if t_batch[i] or i == nsteps - 1:            lastgaelam = r_batch[i, :] - v_batch[i, :]            adv_batch[i, :] = lastgaelam        else:            delta = r_batch[i, :] + m_gamma * v_batch[i + 1, :] - v_batch[i, :]            lastgaelam = delta + m_gamma * m_lambda * lastgaelam            adv_batch[i, :] = lastgaelam    trg_batch = adv_batch + v_batch    # do we standardize the advantage batch?    adv_batch = StandardScaler().fit_transform(adv_batch)    # train    if s_batch.shape[0] > 0:        idx = np.arange(s_batch.shape[0])        for e in range(n_epochs):            np.random.shuffle(idx)            _, actor_summary = m_actor.train(s_batch[idx, :, :], a_batch[idx, :], adv_batch[idx, :])            _, critic_summary = m_critic.train(s_batch[idx, :, :], trg_batch[idx, :])        # update summary        summary_idx = int(m_episode_count)        score_summary = tf.compat.v1.Summary(value=[tf.compat.v1.Summary.Value(tag='Actor/Score', simple_value=np.mean(r_batch))])        m_writer.add_summary(score_summary, summary_idx)        m_writer.add_summary(actor_summary, summary_idx)        m_writer.add_summary(critic_summary, summary_idx)        m_writer.flush()    # save model    try:        with mover_graph.as_default():            saver.save(mover_session, m_model_file, write_meta_graph=False)    except Exception as e:        print(e)# Learn to plandef learn_plans(batch, n_epochs=1):    s_batch, a_batch, r_batch, t_batch, s2_batch, i_batch, w_batch = batch    v_batch = p_critic.predict(s_batch)    n_batch = p_actor.get_neglog(s_batch).reshape(s_batch.shape[0], 1)    adv_batch = np.zeros_like(v_batch)    nsteps = s_batch.shape[0]    for i in reversed(range(nsteps)):        if t_batch[i] or i == nsteps - 1:            lastgaelam = r_batch[i, :] - v_batch[i, :]            adv_batch[i, :] = lastgaelam        else:            delta = r_batch[i, :] + p_gamma * v_batch[i + 1, :] - v_batch[i, :]            lastgaelam = delta + p_gamma * p_lambda * lastgaelam            adv_batch[i, :] = lastgaelam    trg_batch = adv_batch + v_batch    # do we standardize the advantage batch?    adv_batch = StandardScaler().fit_transform(adv_batch)    # train    idx = np.arange(s_batch.shape[0])    for e in range(n_epochs):        np.random.shuffle(idx)        _, actor_summary = p_actor.train(s_batch[idx, :, :], a_batch[idx, :], adv_batch[idx, :], n_batch[idx, :])        _, critic_summary = p_critic.train(s_batch[idx, :, :], trg_batch[idx, :], v_batch)    # update summary    score_summary = tf.compat.v1.Summary(value=[tf.compat.v1.Summary.Value(tag='Actor/Score', simple_value=np.mean(r_batch))])    p_writer.add_summary(score_summary, int(p_episode_count))    p_writer.add_summary(actor_summary, int(p_episode_count))    p_writer.add_summary(critic_summary, int(p_episode_count))    p_writer.flush()    # save model    try:        with planner_graph.as_default():            saver.save(planner_session, p_model_file, write_meta_graph=False)    except Exception as e:        print(e)# Auxiliary functionsdef get_recorded_data(level_file = 'data/level_points.pkl'):    with open(level_file, 'rb') as f:        level_points = pickle.load(f)        key_points = pickle.load(f)        key_stats = pickle.load(f)    RX_raw = []    T_raw = []    RX_resampled = []    deltas = []    Deltas = []    Times = []    for level in level_points:        RX_raw.append(level[0][:-2, :])        T_raw.append(level[1][:-2, :])        tasks = level[2]        for task in tasks:            RX_resampled.append(task[0])            Delta = task[0][1:, :] - task[0][:-1, :]            Deltas.append(Delta)            Times.append(task[1][0] * np.ones((Delta.shape[0], 1)))    RX_raw = np.vstack(RX_raw)    T_raw = np.vstack(T_raw)    RX_resampled = np.vstack(RX_resampled)    for i in range(RX_resampled.shape[0]):        deltas.append(RX_resampled[i + 1:, :] - RX_resampled[:-i - 1, :])    x_min = np.array([-360.0, 3.9024162648733514, 13.252630737652677, 16.775050853637147])    x_max = np.array([360.0, 812.0058600513476, 1011.7128949856826, 787.6024456729566])    print('\nX min: {0}'.format(x_min))    print('X max: {0}\n'.format(x_max))    ss = StandardScaler()    mm = MinMaxScaler()    X_ss = ss.fit_transform(RX_raw[:, 1:])    mm.fit(X_ss)    # find x_min and x_max for X deltas and distances    deltas = np.vstack(deltas)    mm_d = MinMaxScaler()    ss_d = StandardScaler()    mm_d.fit(deltas)    ss_d.fit(deltas)    print('\nDelta min: {0}'.format(mm_d.data_min_))    print('Delta max: {0}\n'.format(mm_d.data_max_))    # generate train datasets for sequence generation and time prediction    Deltas = np.abs(np.vstack(Deltas))    ss_D = StandardScaler()    TX = ss_D.fit_transform(Deltas)    TY = np.vstack(Times)    n_levels = len(level_points)    n_points = len(level_points[0][2][0][0])    n_features = len(level_points[0][2][0][0][0]) - 1    DX = np.zeros((n_levels, n_features))    DY = np.zeros((n_levels, n_points, n_features))    EX = np.zeros((n_levels, n_features))    EY = np.zeros((n_levels, n_points, n_features))    Digs = []    Emps = []    for i in range(n_levels):        dig = mm.transform(ss.transform(level_points[i][2][0][0][:, 1:]))        DX[i, :] = dig[0, :]        DY[i, :, :] = dig        emp = mm.transform(ss.transform(level_points[i][2][1][0][:, 1:]))        EX[i, :] = emp[0, :]        EY[i, :, :] = emp        Digs.append(level_points[i][2][0][0])        Emps.append(level_points[i][2][1][0])    return RX_raw, T_raw, TX, TY, ss_d, mm_d, ss_D, DX, DY, EX, EY, ss, mm, Digs, Emps, key_points, key_stats, x_min, x_maxdef clip_seq(seq, x_min, x_max):    seq = np.clip(        seq,        np.dot(np.ones((seq.shape[0], 1)), x_min.reshape(1, seq.shape[1])),        np.dot(np.ones((seq.shape[0], 1)), x_max.reshape(1, seq.shape[1]))    )    return seqdef generate_user_input(idx):    test_angles = np.arange(65, 205, 20).tolist()    c_idx = int(idx % len(test_angles))    dig_a = test_angles[c_idx]    return dig_adef analyze_user_input(fname='user_input/user_input.txt', a_step=0.25, a_radius=0.5, t_step=1, t_window=10, n_thr=10):    with open(fname, 'r') as f:        lines = f.readlines()    t = []    x = []    for line in lines:        try:            spl = line.split(',')            t.append(float(spl[0]))            x.append([float(item) for item in spl[1:5]])        except:            pass    t = np.array(t)    x = np.array(x)    dig_a_found = None    if len(t) >= n_thr:        d_idx = np.arange(x.shape[0])        td = t[d_idx]        xd = x[d_idx, :]        bucket_angle_dif = []        for t in np.arange(0, np.max(td), t_step):            t_idx = np.where((td >= t) & (td < t + t_window))[0]            if len(t_idx) > 0:                ttime = td[t_idx]                slew = xd[t_idx, 0]                bucket = xd[t_idx, 3]                for x in np.arange(np.min(slew), np.max(slew), a_step):                    a_idx = np.where((slew > x - a_radius) & (slew < x + a_radius))[0]                    if len(a_idx) > 0:                        bucket_angle = bucket[a_idx]                        diff = bucket_angle[-1] - bucket_angle[0]                        if diff > dig_bucket_diff:                            avg_time = np.mean(ttime[a_idx])                            bucket_angle_dif.append([x, avg_time])        if len(bucket_angle_dif) > 0:            bucket_angle_dif = np.array(bucket_angle_dif)            dig_a_found = bucket_angle_dif[np.argmax(bucket_angle_dif[:, 1]), 0]    return dig_a_founddef switch_status():    while True:        try:            requests.post('http://127.0.0.1:5000/mode', json={'mode': 'AI_TRAIN'})            r = requests.get('http://127.0.0.1:5000/mode')            print(r.json())            print('\n******** READY! ********\n')            break        except:            pass        sleep(3)def clean_logs(log_dir):    for the_file in os.listdir(log_dir):        file_path = os.path.join(log_dir, the_file)        try:            if os.path.isfile(file_path):                os.unlink(file_path)        except Exception as e:            print(e)if __name__ == '__main__':    # sequence generator model file    ed_model_file = 'models/trajectory_generator/dense'    # train data    RX_raw, T_raw, TX, TY, ss_d, mm_d, ss_D, DX, DY, EX, EY, ss, mm, Digs, Emps, key_points, key_stats, rx_min, rx_max = get_recorded_data()    cycle_start_point, cycle_end_point, dig_start_point, dig_end_point, emp_start_point, emp_end_point = key_points    dig_bucket_diff, emp_bucket_diff, dig_mean_angle, emp_mean_angle = key_stats    n_levels = DY.shape[0]    n_steps = DY.shape[1]    n_features = DY.shape[2]    # timer, digger and emptier models    timer = TimePredictor(        sequence_generation_graph,        sequence_generation_session,        n_features + 1,        lr = 0.00001    )    digger = SequenceGenerator(        sequence_generation_graph,        sequence_generation_session,        n_features,        n_steps,        lr = 0.00001    )    emptier = SequenceGenerator(        sequence_generation_graph,        sequence_generation_session,        n_features,        n_steps,        lr=0.00001    )    with sequence_generation_graph.as_default():        saver = tf.compat.v1.train.Saver()        try:            saver.restore(sequence_generation_session, ed_model_file)        except Exception as e:            print(e)            sequence_generation_session.run(tf.compat.v1.global_variables_initializer())            timer.train(TX, TY, epochs=100000)            digger.train(DX, DY, epochs=100000)            emptier.train(EX, EY, epochs=100000)            saver.save(sequence_generation_session, ed_model_file, write_meta_graph=False)    # RL agent dimensions    state_dim = 4    action_dim = 4    pid_dim = 3    action_bound = 1    # Actor-critic model for moving    m_time_dim = 1    m_count_max = 16    m_tic = time()    m_target_count = 0    m_episode_count = 0    m_count = 0    m_state = None    m_action = None    m_score = 0    m_action_dim = 3    m_epochs = 1    m_actor_lr = 0.001    m_critic_lr = 0.01    m_gamma = 0.99    m_lambda = 0.95    m_model_file = 'models/mover/a2c'    m_summary_dir = 'logs/mover/'    clean_logs(m_summary_dir)    # episode cache    m_replay_buffer = ReplayBuffer(10000)    m_replay_buffer.clear()    # model    m_actor = Actor(        mover_graph,        mover_session,        (m_time_dim, state_dim),        m_action_dim,        [0.5, 2.5],        m_actor_lr,        policy='dense'    )    m_critic = Critic(        mover_graph,        mover_session,        (m_time_dim, state_dim),        1,        m_critic_lr,        policy='dense'    )    with mover_graph.as_default():        m_saver = tf.compat.v1.train.Saver()        try:            m_saver.restore(mover_session, m_model_file)        except Exception as e:            print(e)            mover_session.run(tf.compat.v1.global_variables_initializer())        m_writer = tf.compat.v1.summary.FileWriter(m_summary_dir, mover_session.graph)    # DDQN model for planning    p_state = None    p_action = None    p_next_state = None    p_next_action = None    p_episode_count = 0    p_count = 0    p_tic = time()    p_actor_lr = 0.01    p_critic_lr = 0.01    p_gamma = 0.99    p_lambda = 0.95    p_clip_range = 0.2    p_model_file = 'models/planner/ppo'    p_summary_dir = 'logs/planner/'    clean_logs(p_summary_dir)    # clean previous user input    open('user_input/user_input.txt','w').close()    # planner action hole    p_time_dim = 4    p_angle_gain = 5    p_stretch_gain = 2    # test_digger()    p_action_vals = [        np.array([0, -1, 1]),        np.array([0, 1, 2, 3]),    ]    p_action_dims = [len(vals) for vals in p_action_vals]    p_action_dim = np.prod(p_action_dims)    p_hole = np.zeros(p_action_dims)    p_actor = Actor_ppo(        planner_graph,        planner_session,        (1, p_action_dim),        p_action_dim,        p_actor_lr,        p_clip_range    )    p_critic = Critic_ppo(        planner_graph,        planner_session,        (1, p_action_dim),        p_action_dim,        p_critic_lr,        p_clip_range    )    with planner_graph.as_default():        p_saver = tf.compat.v1.train.Saver()        try:            p_saver.restore(planner_session, p_model_file)        except Exception as e:            print(e)            planner_session.run(tf.compat.v1.global_variables_initializer())        p_writer = tf.compat.v1.summary.FileWriter(p_summary_dir, planner_session.graph)    p_replay_buffer = ReplayBuffer(10000)    p_replay_buffer_lock = False ### CHANGE TO TRUE TO FREEZE THE PLANNER MODEL    # server    sst = Thread(target=switch_status)    sst.setDaemon(True)    sst.start()    app.run(host='0.0.0.0')