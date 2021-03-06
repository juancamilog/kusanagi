import numpy as np
import theano
import theano.tensor as tt

from kusanagi import utils

m_rng = utils.get_mrng()


def propagate_particles(latent_x, measured_x, pol, dyn, angle_dims=[],
                        iid_per_eval=False, deltas=True, **kwargs):
    ''' Given a set of input states, this function returns predictions for
        the next states. This is done by 1) evaluating the current pol
        2) using the dynamics model to estimate the next state. If x has
        shape [n, D] where n is tthe number of samples and D is the state
        dimension, this function will return x_next with shape [n, D]
        representing the next states and costs with shape [n, 1]
    '''
    # convert angles from input states to their complex representation
    xa1 = utils.gTrig(latent_x, angle_dims)
    xa2 = utils.gTrig(measured_x, angle_dims)

    # compute controls for each sample
    u, sn_u = pol.predict(xa2, iid_per_eval=iid_per_eval,
                          return_samples=True)

    # build state-control vectors
    xu = tt.concatenate([xa1, u], axis=1)

    # predict the change in state given current state-control for each particle
    delta_x, sn_x = dyn.predict(
        xu, iid_per_eval=iid_per_eval, return_samples=True)
    sn_x = theano.gradient.disconnected_grad(sn_x)

    # compute the successor states
    x_next = latent_x + delta_x if deltas else delta_x
    return x_next, sn_x


def rollout(x0, H, gamma0,
            pol, dyn, cost,
            z=None, mm_state=True, mm_cost=True,
            noisy_policy_input=True, noisy_cost_input=True,
            time_varying_cost=False, grad_clip=None, infer_noise_mm=False,
            truncate_gradient=-1, extra_shared=[],
            split_H=1, **kwargs):
    ''' Given some initial state particles x0, and a prediction horizon H
    (number of timesteps), returns a set of trajectories sampled from the
    dynamics model and the discounted costs for each step in the
    trajectory.
    '''
    msg = 'Building computation graph for rollout'
    utils.print_with_stamp(msg, 'mc_pilco.rollout')
    msg = 'Moment-matching [state: %s, cost:%s]'
    msg += ', State measurement noise [policy: %s, cost: %s]'
    opts = (mm_state, mm_cost, noisy_policy_input, noisy_cost_input)
    utils.print_with_stamp(msg % opts, 'mc_pilco.rollout')

    if not time_varying_cost:
        def tv_cost(t, *args, **kwargs):
            return cost(*args, **kwargs)
    else:
        tv_cost = cost

    # define internal scan computations
    def step_rollout(t_next, z1, z2, z2_prev, x, sn, gamma, *args):
        '''
            Single step of rollout.
        '''
        n = x.shape[0]
        n = n.astype(theano.config.floatX)

        # noisy state measruement for control
        xn = x + z2_prev*sn if noisy_policy_input else x

        # get next state distribution
        x_next, sn_next = propagate_particles(
            x, xn, pol, dyn, **kwargs)

        def eval_cost(t, xn, mxn=None, Sxn=None):
            # moment-matching for cost
            if mm_cost:
                # compute input moments
                if mxn is None:
                    mxn = xn.mean(0)
                if Sxn is None:
                    delta = xn - mxn
                    Sxn = delta.T.dot(delta)/(n-1)
                # propagate gaussian through cost (should be implemented in
                # cost func)
                c = tv_cost(t, mxn, Sxn)
                if isinstance(c, list) or isinstance(c, tuple):
                    c = c[0]
            else:
                c = tv_cost(t, xn, None)
            return c

        # if resampling (moment-matching for state)
        if mm_state:
            mx_next = x_next.mean(0)
            delta = x_next - mx_next
            Sx_next = delta.T.dot(delta)/(n-1)
            L = tt.slinalg.cholesky(Sx_next)
            if infer_noise_mm:
                # we will compute a z1 that keeps the same mean and variance
                # after resampling. We do this by standardizing the particles
                # and shuffling them.
                idxs = tt.argsort(z1[:, 0], 0)[::-1]  # random ordering
                z1 = tt.slinalg.solve_lower_triangular(L, delta.T).T
                z1 = theano.gradient.disconnected_grad(z1)[idxs]
            x_next = mx_next + z1.dot(L.T)
            # noisy state measurement for cost
            xn_next = x_next
            if noisy_cost_input:
                xn_next += z2*sn_next
                #  get cost of applying action:
                c_next = eval_cost(t_next, xn_next)
            else:
                c_next = eval_cost(t_next, xn_next, mx_next, Sx_next)
        # no moment-matching for state
        else:
            # noisy state measurement for cost
            xn_next = x_next + z2*sn_next if noisy_cost_input else x_next
            #  get cost of applying action:
            c_next = eval_cost(t_next, xn_next)

        c_next = gamma*c_next

        if grad_clip:
            x_next = theano.gradient.grad_clip(
                x_next, -grad_clip, grad_clip)

        return [c_next, x_next, sn_next, gamma*gamma0]

    # these are the shared variables that will be used in the scan graph.
    # we need to pass them as non_sequences here
    # see: http://deeplearning.net/software/theano/library/scan.html
    nseq = [gamma0]
    nseq.extend(dyn.get_intermediate_outputs())
    nseq.extend(pol.get_intermediate_outputs())
    nseq.extend(extra_shared)

    # loop over the planning horizon
    mode = theano.compile.mode.get_mode('FAST_RUN')
    costs, trajectories = [], [x0[None, :, :]]
    # if split_H > 1, this results in truncated BPTT
    H_ = tt.ceil(H*1.0/split_H).astype('int32')
    for i in range(1, split_H+1):
        start_idx = (i-1)*H_ + 1
        end_idx = start_idx + H_

        output = theano.scan(
            fn=step_rollout, sequences=[tt.arange(start_idx, end_idx),
                                        z[0, start_idx:end_idx],
                                        z[1, start_idx:end_idx],
                                        z[1, -end_idx:-start_idx]],
            outputs_info=[None, x0, 1e-4*tt.ones_like(x0), gamma0],
            non_sequences=nseq, strict=True, allow_gc=False,
            truncate_gradient=H_-truncate_gradient,
            name="mc_pilco>rollout_scan_%d" % i,
            mode=mode)

        rollout_output, rollout_updts = output
        costs_i, trajectories_i = rollout_output[:2]
        costs.append(costs_i)
        trajectories.append(trajectories_i)
        x0 = trajectories_i[-1, :, :]
        x0 = theano.gradient.disconnected_grad(x0)

    costs = tt.concatenate(costs)
    trajectories = tt.concatenate(trajectories)

    trajectories.name = 'trajectories'

    # first axis: batch, second axis: time step
    costs = costs.T
    # first axis; batch, second axis: time step
    trajectories = trajectories.transpose(1, 0, 2)

    return [costs, trajectories], rollout_updts


def get_loss(pol, dyn, cost, angle_dims=[], n_samples=100,
             intermediate_outs=False, mm_state=True, mm_cost=True,
             noisy_policy_input=True, noisy_cost_input=False,
             time_varying_cost=False, resample_dyn=False, crn=True,
             average=True, minmax=False, grad_clip=None, truncate_gradient=-1,
             split_H=1, extra_shared=[], extra_updts_init=None,
             **kwargs):
    '''
        Constructs the computation graph for the value function according to
        the mc-pilco algorithm:
        1) sample x0 from initial state distribution N(mx0,Sx0)
        2) propagate the state particles forward in time
            2a) compute controls for eachc particle
            2b) use dynamics model to predict next states for each particle
            2c) compute cost for each particle
        3) return the expected value of the sum of costs
        @param pol
        @param dyn
        @param cost
        @param angle_dims angle dimensions that should be converted to complex
                          representation
        @param n_samples number of samples for Monte Carlo integration
        @param intermediate_outs whether to also return the per-timestep costs
                                 and rolled out trajectories
        @param mm_state whether to resample state particles, at each time step,
                  from a moment matched Gaussian distribution
        @param mm_cost whether to push the moment matched state distribution
                       through the cost function
        @noisy_policy_input whether to corrupt the state particles, with the
                            dynamics model measurement noise, before passing
                            them as input to the policy
        @noisy_cost_input whether to corrupt the state particles, with the
                          dynamics model measurement noise, before passing them
                          as input to the cost function
        @time_varying_cost whether the cost function requires a time index.
                           If True, the cost function will be called as
                           cost(t, x); i.e. the first argument will be the
                           timestep index t.
        @param crn wheter to use common random numbers.
        @return Returns a tuple of (outs, inps, updts). These correspond to the
                output variables, input variables and updates dictionary, if
                any.
                By default, the only output variable is the value.
    '''
    # get angle dims from policy, if any
    if len(angle_dims) == 0 and hasattr(pol, 'angle_dims'):
        angle_dims = pol.angle_dims
    # make sure that the dynamics model has the same number of samples
    if hasattr(dyn, 'update'):
        dyn.update(n_samples)
    if hasattr(pol, 'update'):
        pol.update(n_samples)

    # initial state distribution
    mx0 = tt.vector('mx0')
    Sx0 = tt.matrix('Sx0')

    # prediction horizon
    H = tt.iscalar('H')
    # discount factor
    gamma = tt.scalar('gamma')
    # how many times we've done a forward pass
    n_evals = theano.shared(0)
    # new samples with every rollout
    z = m_rng.normal((2, H+1, n_samples, mx0.shape[0]))

    # sample random numbers to be used in the rollout
    updates = theano.updates.OrderedUpdates()
    if crn:
        utils.print_with_stamp(
            "Using common random numbers for moment matching",
            'mc_pilco.rollout')
        # we reuse samples and resamples every crn iterations
        # resampling is done to avoid getting stuck with bad solutions
        # when we get unlucky.
        crn = 500 if type(crn) is not int else crn
        utils.print_with_stamp(
            "CRNs will be resampled every %d rollouts" % crn,
            "mc_pilco.rollout")
        z_resampled = z
        z_init = np.random.normal(
            size=(2, 1000, n_samples, dyn.E)).astype(theano.config.floatX)
        z = theano.shared(z_init)
        updates[z] = theano.ifelse.ifelse(
            tt.eq(n_evals % crn, 0), z_resampled, z)
        updates[n_evals] = n_evals + 1

        # now we will make sure that z is has the correct shape
        z = theano.ifelse.ifelse(
            z.shape[1] < H,
            tt.tile(z, (1, tt.ceil(H/z.shape[0]).astype('int64'), 1, 1)),
            z
        )[:H+1]

    # draw initial set of particles
    z0 = m_rng.normal((n_samples, mx0.shape[0]))
    Lx0 = tt.slinalg.cholesky(Sx0)
    x0 = mx0 + z0.dot(Lx0.T)

    if pol.Xm is None:
        # try to normalize policy inputs (output is implicitly normalized)
        Xm = dyn.Xm[:pol.D]
        Xc = tt.cov(dyn.X[:, :pol.D]-Xm, rowvar=False, ddof=1)
        iXs = tt.slinalg.cholesky(tt.nlinalg.matrix_inverse(Xc))
        pol.set_params(dict(Xm=Xm.eval(), iXs=iXs.eval()), trainable=False)

        # ensure we're always using the same scaling as the dynamics model
        updates[pol.Xm] = Xm
        updates[pol.iXs] = iXs

    # get rollout output
    r_outs, updts = rollout(x0, H, gamma,
                            pol, dyn, cost,
                            angle_dims=angle_dims,
                            z=z,
                            mm_state=mm_state,
                            iid_per_eval=resample_dyn,
                            mm_cost=mm_cost,
                            truncate_gradient=truncate_gradient,
                            split_H=split_H,
                            noisy_policy_input=noisy_policy_input,
                            noisy_cost_input=noisy_cost_input,
                            time_varying_cost=time_varying_cost,
                            extra_shared=extra_shared, **kwargs)

    costs, trajectories = r_outs
    acc_costs = costs.mean(-1, keepdims=True) if average\
        else costs.sum(-1, keepdims=True)
    if minmax and not mm_cost:
        temp = acc_costs.std()
        utils.print_with_stamp(
            "Using softmax loss", 'mc_pilco.rollout')
        weights = tt.nnet.softmax((acc_costs - acc_costs.mean(0)).T/temp).T
        weights_ = theano.gradient.disconnected_grad(weights)
        wcosts = costs*weights_
        loss = wcosts.sum(0).mean() if average else wcosts.sum(0).sum()
        entropy = -(weights*tt.log(weights)).sum()
        reg_weight = -1e-3
        loss += reg_weight*entropy
    else:
        # loss is E_{dyns}((1/H)*sum c(x_t))
        #          = (1/H)*sum E_{x_t}(c(x_t))
        loss = acc_costs.mean()

    inps = [mx0, Sx0, H, gamma]
    updates += updts
    if callable(extra_updts_init):
        updates += extra_updts_init(loss, costs, trajectories)
    if intermediate_outs:
        return [loss, costs, trajectories], inps, updates
    else:
        return loss, inps, updates


def build_rollout(*args, **kwargs):
    kwargs['intermediate_outs'] = True
    outs, inps, updts = get_loss(*args, **kwargs)
    rollout_fn = theano.function(inps, outs, updates=updts,
                                 allow_input_downcast=True)
    return rollout_fn
