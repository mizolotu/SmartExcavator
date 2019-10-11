import pickle, json, os, requests, loggingimport numpy as npimport tensorflow as tffrom time import time, sleepfrom sklearn.preprocessing import StandardScaler, MinMaxScalerfrom flask import Flask, request, jsonifyfrom threading import Threadfrom sequence_generator import TimePredictor, SequenceGeneratorfrom a2c import Actor, Criticfrom dqn import DDQNfrom replay_buffer import ReplayBufferfrom matplotlib import pyplot as pp# switch off CUDAos.environ["CUDA_VISIBLE_DEVICES"]="-1"# init Flask appapp = Flask(__name__)log = logging.getLogger('werkzeug')log.disabled = True# tf graphs and sessionssequence_generation_graph = tf.Graph()sequence_generation_session = tf.compat.v1.Session(graph=sequence_generation_graph)mover_graph = tf.Graph()mover_session = tf.compat.v1.Session(graph=mover_graph)planner_graph = tf.Graph()planner_session = tf.compat.v1.Session(graph=planner_graph)# other global variablesmode = 'USER'pause = False# excavator API@app.route('/mode', methods=['GET', 'POST'])def get_mode():    # global variables    global mode, p_count, p_tic, p_episode_count, p_action, p_hole, m_tic, m_target_count, m_count, m_episode_count, m_action, m_state, m_score, dig_angle    # GET request    if request.method == 'GET':        # GET request when under user control        if mode == 'USER':            current_dig_angle = analyze_user_input()        # GET request when under AI control        else:            current_dig_angle = dig_angle    # POST request    elif request.method == 'POST':        data = request.data.decode('utf-8')        jdata = json.loads(data)        new_mode = jdata['mode']        # switching to the user control        if new_mode == 'USER':            current_dig_angle = None        # switching to the AI control        elif new_mode.startswith('AI'):            # AI training            if new_mode.endswith('TRAIN'):                dig_angle = generate_user_input(p_count)            # AI testing            elif new_mode.endswith('TEST'):                dig_angle = analyze_user_input(fname='user_input/user_input.txt') # CHANGE THIS TO THE FILE WITH REAL USER INPUT!            # if dig angle is set            if dig_angle is not None:                current_dig_angle = dig_angle                # nulify states and actions                p_tic = time()                p_count = 0                p_episode_count = 0                p_action = None                p_hole = np.zeros(p_action_dims)                m_tic = time()                m_target_count = 0                m_count = 0                m_episode_count = 0                m_state = None                m_action = None                m_score = np.zeros(action_dim)            # otherwise            else:                # clean previous user input                open('user_input/user_input.txt','w').close()                # do not do anything                new_mode = 'USER'                current_dig_angle = None        else:            # unknown mode            current_dig_angle = None        # update the mode        mode = new_mode    data_to_return = {'mode': mode, 'dig_angle': current_dig_angle}    return jsonify(data_to_return)def moving_average(x, step=2, window=2):    seq = []    n = x.shape[0]    for i in np.arange(0, n, step):        idx = np.arange(np.maximum(0, i - window), np.minimum(n - 1, i + window + 1))        seq.append(np.mean(x[idx, :], axis=0))    return np.vstack(seq)def generate_sequence(model, rx_start, n_points, stretch=None):    a_start = rx_start[0]    x_start = rx_start[1:]    latent = mm.transform(ss.transform(x_start.reshape(1, n_features)))    next_steps = model.predict(latent)    y = ss.inverse_transform(mm.inverse_transform(next_steps.reshape(n_points, n_features)))    seq = np.hstack([a_start * np.ones((n_points, 1)), y])    seq = moving_average(seq)    idx_s = [0, 0, np.argmin(seq[:, 3]), 0]    if stretch is not None:        for i in np.arange(1,action_dim):            mu = seq[idx_s[i], i]            si = np.std(seq[:, i])            z = (seq[:, i] - mu) / (si + 1e-6)            idx = np.where(z <= 0)[0]            seq[idx, i] = mu + (1 + stretch[i]) * si * z[idx]    seq = moving_average(seq, step=1, window=2)    return seqdef interp_x_by_r(seq_start, seq_end, n_steps):    r = np.hstack([seq_start[0], seq_end[0]])    step = (seq_end[0] - seq_start[0]) / (n_steps - 1)    r_ = seq_start[0] + step * np.arange(n_steps)    seq = np.zeros((n_steps, len(seq_start)))    seq[:, 0] = r_    for i in range(1, len(seq_start)):        x = np.hstack([seq_start[i], seq_end[i]])        seq[:, i] = np.interp(r_, r, x)    return seqdef train_trajectory(last_step):    ddists = [np.sum((last_step[1:] - dig[0, 1:]) ** 2) for dig in Digs]    dig = Digs[np.argmin(ddists)]    dig = moving_average(dig)    n_steps = dig.shape[0]    to_dig = interp_x_by_r(cycle_start_point, dig[0, :], n_steps)    dists = [np.sum((emp[0, 1:] - dig[-1, 1:]) ** 2) for emp in Emps]    emp = Emps[np.argmin(dists)]    dig_to_emp = interp_x_by_r(dig[-1, :], emp[0, :], n_steps)    trajectory_ = np.vstack([        to_dig,        dig,        dig_to_emp,        emp    ])    dists = [np.sum((point - last_step) ** 2) for point in trajectory_]    l = len(trajectory_)    idx = np.argmin(dists)    trajectory = []    trajectory.append(last_step)    for i in range(idx, l):        trajectory.append(trajectory_[i, :])    for i in range(0, idx):        trajectory.append(trajectory_[i, :])    trajectory = np.vstack(trajectory)    print(trajectory.shape)    for t in trajectory:        print(t)    return trajectorydef build_trajectory(last_point, dig):    n_steps = dig.shape[0]    to_dig = interp_x_by_r(last_point, dig[0, :], n_steps + 1)    dists = [emp[0, 3] for emp in Emps]    emp = Emps[np.argmax(dists)]    #emp = Emps[np.random.randint(0, len(Emps))]    emp = moving_average(emp)    dig_to_emp = interp_x_by_r(np.hstack([dig[0, 0], dig_start_point[1:]]), emp[0, :], n_steps)    trajectory = np.vstack([        to_dig[np.array([0, -1]), :],        dig,        dig_to_emp[np.array([0, -1]), :],        emp    ])    return trajectorydef predict_time(seq):    deltas = np.abs(seq[1:, :] - seq[:-1, :])    deltas_std = ss_D.transform(deltas)    t = timer.predict(deltas_std)    t = np.clip(t, np.min(TY), np.max(TY))    return tdef test_digger():    random_last_step_idx = np.random.randint(0, len(DX))    last_step = DX[random_last_step_idx, :].reshape(1, -1)    x = ss.inverse_transform(mm.inverse_transform(last_step))    x = np.hstack([90, x.reshape(n_features)])    y0 = generate_sequence(digger, x, n_points=n_steps, stretch=None)    yd1 = generate_sequence(digger, x, n_points=n_steps, stretch = np.array([0, 1 * depth_step, 0, 0]))    ys1 = generate_sequence(digger, x, n_points=n_steps, stretch=np.array([0, 0, 1 * swing_step, 0]))    ysm1 = generate_sequence(digger, x, n_points=n_steps, stretch = np.array([0, 0, 0, 1 * grab_step]))    pp.plot(y0, '--')    pp.plot(yd1)    pp.show()    pp.plot(y0, '--')    pp.plot(ys1)    pp.show()    pp.plot(y0, '--')    pp.plot(ysm1)    pp.show()@app.route('/trajectory')def get_trajectory():    # global variables    global p_tic, p_state, p_action, p_hole, p_count, p_episode_count, dig_angle    # request and response data    data = request.data.decode('utf-8')    jdata = json.loads(data)    x = jdata['x']  # last position    score = jdata['score']  # ground taken    data_to_send = {'y': [], 't': []}    # plan the next action    if p_action is not None:        hole_idx = np.unravel_index(p_action.argmax(), p_action_dims)        p_hole[hole_idx] = score    p_next_state = p_hole.reshape(1, 1, p_action_dim)    if mode == 'AI_TRAIN':        # the first action in the training mode is always the same        if p_count == 0:            p_next_action = np.zeros((1, p_action_dim))        elif p_count == time_dim:            done = True        elif np.random.rand() > p_eps_start + p_count * p_eps_step:            p_next_action = np.random.rand(1, p_action_dim)            print('\nRANDOM ACTION! Optimal would be {0}\n'.format(np.unravel_index(planner.predict(p_state).argmax(), p_action_dims)))    # in the testing mode just act    elif mode == 'TEST':        p_next_action = planner.predict(p_state)    # transofrm planner action to the full trajectory:    print('\nPlanner:')    print('Trajectory completed in {0} seconds'.format(time() - p_tic))    print('Episode {0}:'.format(p_episode_count))    print('Iteration {0}:'.format(p_count))    print('Score: {0}\n'.format(score))    idx = np.unravel_index(p_next_action.argmax(), p_action_dims)    angle = dig_angle + p_action_vals[0][idx[0]] * p_angle_gain    last_step = np.array(x)    dig_first_step = np.hstack([angle, x[1:]])    stretch = np.zeros(action_dim)    for i in np.arange(1, action_dim):        stretch[i] = p_action_vals[i][idx[i]] * p_stretch_gain / time_dim    print('\nPlanner action at {0}: {1} with angle delta {2} and stretch {3}\n'.format(p_count, idx, p_action_vals[0][idx[0]], stretch[1:]))    dig = generate_sequence(digger, dig_first_step, n_points=n_steps, stretch=stretch)    full_trajectory = build_trajectory(last_step, dig)    full_trajectory = clip_seq(full_trajectory, rx_min, rx_max)    trajectory_time = predict_time(full_trajectory)    trajectory_time = trajectory_time.reshape(trajectory_time.shape[0])    for ti, tt in enumerate(zip(full_trajectory, trajectory_time)):        print(ti, tt[0], tt[1])    print('Time for the trajectory: {0}'.format(np.sum(trajectory_time)))    p_tic = time()    if p_action is not None:        p_count += 1        if p_count > 1:            p_action_hot = np.zeros_like(p_action)            p_action_hot[0, p_action.argmax()] = 1            if not p_replay_buffer_lock:                p_replay_buffer.add(                    np.reshape(p_state, (1, p_action_dim)),                    np.reshape(p_action_hot, (p_action_dim,)),                    score,                    done,                    np.reshape(p_next_state, (1, p_action_dim))                )        # check finished or not        if done:            # learn to plan            learning_thread = Thread(target=learn_plans, args=(planner_graph, planner_session))            learning_thread.setDaemon(True)            learning_thread.start()            # nulify state            p_hole = np.zeros(p_action_dims)            p_state = None            p_action = None            p_count = 0            # increment episode count            dig_angle = generate_user_input(p_count / time_dim)            p_episode_count += 1        else:            p_state = np.array(p_next_state)            p_action = np.array(p_next_action)    data_to_send['y'] = full_trajectory[1:, :].tolist()    data_to_send['t'] = trajectory_time.tolist()    return jsonify(data_to_send)@app.route('/controls')def get_controls():    # global variables    global m_tic, m_state, m_action, m_count, m_episode_count, m_score, m_target_count    # request and response data    data = request.data.decode('utf-8')    jdata = json.loads(data)    deltas = np.array(jdata['deltas'])    delta_start = np.array(jdata['delta_start'])    delta_end = np.array(jdata['delta_end'])    in_target = jdata['in_target']    time_passed = jdata['time']    time_limit = jdata['time_limit']    done = jdata['done']    data_to_send = {}    # calculate score    tol = 1e-8    error_thr = 3    if np.all(delta_end) <= error_thr or m_count == m_count_max:        delta_start_std = np.maximum(mm_d.transform(delta_start.reshape(1, action_dim)), np.zeros((1,action_dim)))        delta_end_std = np.maximum(mm_d.transform(delta_end.reshape(1, action_dim)), np.zeros((1, action_dim)))        dist_covered = np.maximum(delta_start_std - delta_end_std, np.zeros((1, action_dim)))        component_score = dist_covered.reshape(action_dim) / (1000 * np.maximum(time_limit, time() - m_tic) + tol)  # distance covered per milisecond        if np.all(delta_end) <= error_thr:            m_target_count += 1        m_tic = time()    else:        component_score = np.zeros(action_dim)    pid_score = np.hstack([component_score for _ in range(pid_dim)])    m_score += component_score    # predict next action    m_next_state = mm_d.transform(deltas)    m_next_action = m_actor.predict(np.reshape(m_next_state, (1, time_dim, state_dim)))    act_values = m_next_action[0].reshape(pid_dim, action_dim)    data_to_send['controls'] = act_values.tolist()    # check if action is not none and there is need for a action, i.e. deltas are greater than threshold    if m_action is not None:        if not in_target and time_passed > tol:            m_replay_buffer.add(                m_state,                np.reshape(m_action, (pid_dim * action_dim,)),                pid_score,                done,                m_next_state            )            m_count += 1            if not m_monte_carlo:                m_episode_count += 1                batch = m_replay_buffer.sample_batch(1, rnd=False)                learning_thread = Thread(target=learn_moves, args=(batch,))                learning_thread.setDaemon(True)                learning_thread.start()                m_replay_buffer.clear()        else:            print('Skip the sample!')    # check finished or not    if m_count == m_count_max:        # print stats        print('During episode {0}, {1} targets have been reached in {2} steps with score: {3}'.format(m_episode_count, m_target_count, m_count, m_score))        # learn to move        if m_monte_carlo:            try:                batch = m_replay_buffer.sample_batch(m_count, rnd=False)                learning_thread = Thread(target=learn_moves, args=(batch,))                learning_thread.setDaemon(True)                learning_thread.start()            except Exception as e:                print(e)            m_replay_buffer.clear()        # nulify state        #m_state = None        #m_action = None        m_score = np.zeros(action_dim)        m_count = 0        m_target_count = 0        m_episode_count += 1    #else:    m_state = np.array(m_next_state)    m_action = np.array(m_next_action)    return jsonify(data_to_send)# Learn to movedef learn_moves(batch, n_epochs=1):    s_batch, a_batch, r_batch, t_batch, s2_batch, i_batch, w_batch = batch    if m_monte_carlo:        v_batch = m_critic.predict(s_batch)        adv_batch = np.zeros_like(v_batch)        nsteps = s_batch.shape[0]        for i in reversed(range(nsteps)):            if t_batch[i] or i == nsteps - 1:                lastgaelam = r_batch[i, :] - v_batch[i, :]                adv_batch[i, :] = lastgaelam            else:                delta = r_batch[i, :] + m_gamma * v_batch[i + 1, :] - v_batch[i, :]                lastgaelam = delta + m_gamma * m_lambda * lastgaelam                adv_batch[i, :] = lastgaelam        trg_batch = adv_batch + v_batch        # do we standardize the advantage batch?        # adv_batch = StandardScaler().fit_transform(adv_batch)    else:        v_batch = m_critic.predict(s_batch)        v2_batch = m_critic.predict(s2_batch)        trg_batch = np.zeros_like(v_batch)        nsteps = s_batch.shape[0]        for i in reversed(range(nsteps)):            if t_batch[i] or i == nsteps - 1:                trg_batch[i, :] = r_batch[i, :]            else:                trg_batch[i, :] = r_batch[i, :] + m_gamma * v2_batch[i, :]        adv_batch = trg_batch - v_batch  # this is not advantages, just td error    # train    idx = np.arange(s_batch.shape[0])    for e in range(n_epochs):        np.random.shuffle(idx)        _, actor_summary = m_actor.train(s_batch[idx, :, :], a_batch[idx, :], adv_batch[idx, :])        _, critic_summary = m_critic.train(s_batch[idx, :, :], trg_batch[idx, :])    # update summary    cnames = ['Slew', 'Boom', 'Arm', 'Bucket']    pnames = ['P', 'I', 'D']    score_summaries = []    pid_summaries = []    for i in range(action_dim):        score_summaries.append(tf.compat.v1.Summary(value=[tf.compat.v1.Summary.Value(tag='Score/{0}'.format(cnames[i]), simple_value=np.mean(r_batch[:, i]))]))    for i in range(pid_dim):        for j in range(action_dim):            pid_summaries.append(tf.compat.v1.Summary(value=[tf.compat.v1.Summary.Value(tag='Gain/{0}/{1}'.format(pnames[i], cnames[j]), simple_value=np.mean(a_batch[:, i * action_dim + j]))]))    for score_summary in score_summaries:        m_writer.add_summary(score_summary, int(m_episode_count))    for pid_summary in pid_summaries:        m_writer.add_summary(pid_summary, int(m_episode_count))    m_writer.add_summary(actor_summary, int(m_episode_count))    m_writer.add_summary(critic_summary, int(m_episode_count))    m_writer.flush()    # save model    if m_episode_count % n_epochs == 0:  # this is spaghetti coding right here!        try:            with mover_graph.as_default():                saver.save(mover_session, m_model_file, write_meta_graph=False)        except Exception as e:            print(e)# Learn to plandef learn_plans(graph, sess, n_epochs=4):    if p_replay_buffer.size() >= p_minibatch_size:        for e in range(n_epochs):            s_batch, a_batch, r_batch, t_batch, s2_batch, i_batch, w_batch = p_replay_buffer.sample_batch(                p_minibatch_size, empty_queue=True            )            q_next_state = planner.predict(s2_batch)            q_target_next_state = planner.predict_target(s2_batch)            y_i = []            for k in range(p_minibatch_size):                if t_batch[k]:                    y_i.append(r_batch[k])                else:                    y_i.append(r_batch[k] + planner.gamma * q_target_next_state[k, q_next_state[k, :].argmax()])            predicted_q_value, abs_errors, _, summary = planner.train(                s_batch,                np.reshape(a_batch, (p_minibatch_size, p_action_dim)),                np.reshape(y_i, (p_minibatch_size, 1)),                w_batch            )            p_replay_buffer.update_priorities(i_batch, abs_errors[:, 0])            planner.update_target_network()        # updaty summary        p_writer.add_summary(summary, int(p_episode_count))        p_writer.flush()        # save model        try:            with planner_graph.as_default():                p_saver.save(planner_session, p_model_file, write_meta_graph=False)        except Exception as e:            print(e)        # save buffer        try:            p_replay_buffer.save_buffer(p_memory_file)        except Exception as e:            print(e)# Auxiliary functionsdef get_recorded_data(level_file = 'data/level_points.pkl'):    with open(level_file, 'rb') as f:        level_points = pickle.load(f)        key_points = pickle.load(f)        key_stats = pickle.load(f)    RX_raw = []    deltas = []    Deltas = []    Times = []    for level in level_points:        RX_raw.append(level[0])        for i in range(3):            deltas.append(level[0][i+1:, :] - level[0][:-i-1, :])        tasks = level[2]        for task in tasks:            Delta = task[0][1:, :] - task[0][:-1, :]            Deltas.append(Delta)            Times.append(task[1][0] * np.ones((Delta.shape[0], 1)))    RX_raw = np.vstack(RX_raw)    x_min = np.array([-360.0, 3.9024162648733514, 13.252630737652677, 16.775050853637147]) # np.min(RX_raw, axis=0)    x_max = np.array([360.0, 812.0058600513476, 1011.7128949856826, 787.6024456729566]) # np.max(RX_raw, axis=0)    print('\nX min: {0}'.format(x_min, x_max))    print('X max: {0}\n'.format(x_max))    ss = StandardScaler()    mm = MinMaxScaler()    X_ss = ss.fit_transform(RX_raw[:, 1:])    mm.fit(X_ss)    # find x_min and x_max for X deltas and distances    deltas = np.abs(np.vstack(deltas))    mm_d = MinMaxScaler()    mm_d.fit(deltas)    # generate train datasets for sequence generation and time prediction    Deltas = np.abs(np.vstack(Deltas))    ss_D = StandardScaler()    TX = ss_D.fit_transform(Deltas)    TY = np.vstack(Times)    n_levels = len(level_points)    n_points = len(level_points[0][2][0][0])    n_features = len(level_points[0][2][0][0][0]) - 1    DX = np.zeros((n_levels, n_features))    DY = np.zeros((n_levels, n_points, n_features))    EX = np.zeros((n_levels, n_features))    EY = np.zeros((n_levels, n_points, n_features))    Digs = []    Emps = []    for i in range(n_levels):        dig = mm.transform(ss.transform(level_points[i][2][0][0][:, 1:]))        DX[i, :] = dig[0, :]        DY[i, :, :] = dig        emp = mm.transform(ss.transform(level_points[i][2][1][0][:, 1:]))        EX[i, :] = emp[0, :]        EY[i, :, :] = emp        Digs.append(level_points[i][2][0][0])        Emps.append(level_points[i][2][1][0])    return TX, TY, mm_d, ss_D, DX, DY, EX, EY, ss, mm, Digs, Emps, key_points, key_stats, x_min, x_maxdef clip_seq(seq, x_min, x_max):    seq = np.clip(        seq,        np.dot(np.ones((seq.shape[0], 1)), x_min.reshape(1, seq.shape[1])),        np.dot(np.ones((seq.shape[0], 1)), x_max.reshape(1, seq.shape[1]))    )    return seqdef generate_user_input(idx):    test_angles = [65, 92.5, 120, 180]    c_idx = int(idx % len(test_angles))    dig_a = test_angles[c_idx]    return dig_adef analyze_user_input(fname='user_input/user_input.txt', a_step=0.25, a_radius=0.5, t_step=1, t_window=10, n_thr=10):    with open(fname, 'r') as f:        lines = f.readlines()    t = []    x = []    for line in lines:        try:            spl = line.split(',')            t.append(float(spl[0]))            x.append([float(item) for item in spl[1:5]])        except:            pass    t = np.array(t)    x = np.array(x)    dig_a_found = None    if len(t) >= n_thr:        d_idx = np.arange(x.shape[0])        td = t[d_idx]        xd = x[d_idx, :]        bucket_angle_dif = []        for t in np.arange(0, np.max(td), t_step):            t_idx = np.where((td >= t) & (td < t + t_window))[0]            if len(t_idx) > 0:                ttime = td[t_idx]                slew = xd[t_idx, 0]                bucket = xd[t_idx, 3]                for x in np.arange(np.min(slew), np.max(slew), a_step):                    a_idx = np.where((slew > x - a_radius) & (slew < x + a_radius))[0]                    if len(a_idx) > 0:                        bucket_angle = bucket[a_idx]                        diff = bucket_angle[-1] - bucket_angle[0]                        if diff > dig_bucket_diff:                            avg_time = np.mean(ttime[a_idx])                            bucket_angle_dif.append([x, avg_time])        if len(bucket_angle_dif) > 0:            bucket_angle_dif = np.array(bucket_angle_dif)            dig_a_found = bucket_angle_dif[np.argmax(bucket_angle_dif[:, 1]), 0]    return dig_a_founddef switch_status():    while True:        try:            requests.post('http://127.0.0.1:5000/mode', json={'mode': 'AI_TRAIN'})            r = requests.get('http://127.0.0.1:5000/mode')            print(r.json())            print('\n******** READY! ********\n')            break        except:            pass        sleep(3)def clean_logs(log_dir):    for the_file in os.listdir(log_dir):        file_path = os.path.join(log_dir, the_file)        try:            if os.path.isfile(file_path):                os.unlink(file_path)        except Exception as e:            print(e)if __name__ == '__main__':    # sequence generator model file    ed_model_file = 'models/trajectory_generator/dense'    # train data    TX, TY, mm_d, ss_D, DX, DY, EX, EY, ss, mm, Digs, Emps, key_points, key_stats, rx_min, rx_max = get_recorded_data()    cycle_start_point, cycle_end_point, dig_start_point, dig_end_point, emp_start_point, emp_end_point = key_points    dig_bucket_diff, emp_bucket_diff, dig_mean_angle, emp_mean_angle = key_stats    n_levels = DY.shape[0]    n_steps = DY.shape[1]    n_features = DY.shape[2]    # timer, digger and emptier models    timer = TimePredictor(        sequence_generation_graph,        sequence_generation_session,        n_features + 1,        lr = 0.00001    )    digger = SequenceGenerator(        sequence_generation_graph,        sequence_generation_session,        n_features,        n_steps,        lr = 0.00001    )    emptier = SequenceGenerator(        sequence_generation_graph,        sequence_generation_session,        n_features,        n_steps,        lr=0.00001    )    with sequence_generation_graph.as_default():        saver = tf.compat.v1.train.Saver()        try:            saver.restore(sequence_generation_session, ed_model_file)        except Exception as e:            print(e)            sequence_generation_session.run(tf.compat.v1.global_variables_initializer())            print(TX.shape, TY.shape)            timer.train(TX, TY, epochs=100000)            digger.train(DX, DY, epochs=100000)            emptier.train(EX, EY, epochs=100000)            saver.save(sequence_generation_session, ed_model_file, write_meta_graph=False)    # RL agent dimensions    time_dim = 4    state_dim = 4    action_dim = 4    pid_dim = 3    action_bound = 1    # Actor-critic model for moving    m_count_max = 64    m_tic = time()    m_target_count = 0    m_episode_count = 0    m_count = 0    m_state = None    m_action = None    m_score = np.zeros(action_dim)    m_actor_lr = 0.001    m_critic_lr = 0.001    m_gamma = 0.99    m_lambda = 0.95    m_monte_carlo = True    m_model_file = 'models/mover/a2c'    m_summary_dir = 'logs/mover/'    m_memory_file = 'memories/mover.pkl'    clean_logs(m_summary_dir)    # episode cache    m_replay_buffer = ReplayBuffer(10000)    m_replay_buffer.clear()    # model    m_actor = Actor(        mover_graph,        mover_session,        (time_dim, state_dim),        action_dim * pid_dim,        action_bound,        m_actor_lr,        policy='lstm'    )    m_critic = Critic(        mover_graph,        mover_session,        (time_dim, state_dim),        action_dim * pid_dim,        m_critic_lr,        policy='lstm'    )    with mover_graph.as_default():        m_saver = tf.compat.v1.train.Saver()        try:            m_saver.restore(mover_session, m_model_file)        except Exception as e:            print(e)            mover_session.run(tf.compat.v1.global_variables_initializer())        m_writer = tf.compat.v1.summary.FileWriter(m_summary_dir, mover_session.graph)    # DDQN model for planning    p_state = None    p_action = None    p_episode_count = 0    p_count = 0    p_tic = time()    p_critic_lr = 0.01    p_gamma = 0.99    p_tau = 0.25    p_minibatch_size = 32    p_model_file = 'models/planner/ddqn'    p_summary_dir = 'logs/planner/'    p_memory_file = 'memories/planner.pkl'    clean_logs(p_summary_dir)        # clean previous user input    open('user_input/user_input.txt','w').close()    # planner action hole    p_angle_gain = 7.5    p_stretch_gain = 2.5    p_eps_start = 0.2    p_eps_step = 0.1    depth_step = 1.0 / time_dim    swing_step = 1.0 / time_dim    grab_step = 1.0 / time_dim    p_action_vals = [        np.array([0, -1, 1]),        np.array([1, 2, 3, 4]),        np.array([1, 2, 3, 4]),        np.array([1, 2, 3, 4])    ]    p_action_dims = [len(vals) for vals in p_action_vals]    p_action_dim = np.prod(p_action_dims)    p_hole = np.zeros(p_action_dims)    # test_digger()    planner = DDQN(        planner_graph,        planner_session,        (1, p_action_dim),        p_action_dim,        p_critic_lr,        p_tau,        p_gamma    )    with planner_graph.as_default():        p_saver = tf.compat.v1.train.Saver()        try:            p_saver.restore(planner_session, p_model_file)        except Exception as e:            print(e)            planner_session.run(tf.compat.v1.global_variables_initializer())        p_writer = tf.compat.v1.summary.FileWriter(p_summary_dir, planner_session.graph)    p_replay_buffer = ReplayBuffer(10000)    try:        p_replay_buffer.load_buffer(p_memory_file)    except Exception as e:        print(e)    p_replay_buffer_lock = True ### CHANGE TO TRUE TO FREEZE THE PLANNER MODEL    # server    sst = Thread(target=switch_status)    sst.setDaemon(True)    sst.start()    app.run(host='0.0.0.0')