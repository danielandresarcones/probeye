# ============================================================================ #
#                                   Imports                                    #
# ============================================================================ #

# standard library imports
import copy as cp

# third party imports
import numpy as np
import torch as th
from pyro.infer import NUTS, MCMC
import arviz as az

# local imports
from probeye.inference.torch_.priors import translate_prior_template
from probeye.inference.torch_.noise_models import translate_noise_model
from probeye.subroutines import len_or_one

# ============================================================================ #
#                          pyro/torch sampling solver                          #
# ============================================================================ #

def run_pyro_solver(problem_ori, n_walkers=1, n_steps=300, n_initial_steps=30,
                    step_size=0.1, verbose=True):
    """
    Solves an inference problem described in problem using pyro/torch.

    Parameters
    ----------
    problem_ori : obj[InferenceProblem]
        Describes the inference problem including e.g. parameters and data.
    n_walkers : int, optional
        Number of walkers used by the estimator.
    step_size : float, optional
        The step size of the NUTS sampler.
    n_steps: int, optional
        The number of steps the sampler takes.
    n_initial_steps: int, optional
        The number of steps for the burn-in phase.
    verbose : bool, optional
        No logging output when False. More logging information when True.
        
    Returns
    -------
    inference_data : obj[arviz.data.inference_data.InferenceData]
        Contains the results of the sampling procedure.
    """

    # a copy is created here, so that this solver routine does not have side
    # effects on the original problem; such side effects would occur due to
    # calling the assign_experiments_to_noise_models-method below
    problem = cp.deepcopy(problem_ori)
    problem = problem.transform_experimental_data(f=th.from_numpy)

    # each noise model must be connected to the relevant experiment_names
    problem.assign_experiments_to_noise_models()

    # the dictionary dependency_dict will contain all latent parameter names as
    # keys; the value of each key will be a list with latent hyperparameters of
    # the latent parameter's prior
    dependency_dict = dict()
    for prm_name in problem.parameters.latent_prms:
        dependency_dict[prm_name] = []
        hyperparameters = problem.parameters[prm_name].prior.hyperparameters
        for prior_prm_name in hyperparameters:
            if prior_prm_name in problem.parameters.latent_prms:
                dependency_dict[prm_name].append(prior_prm_name)

    # this makes sure that the items in dependency are in an order that they can
    # be sampled from beginning (index 0) sequentially until the last item
    # without encountering any dependency problems (i.e. that another parameter
    # has to be sampled before the considered parameter can be sampled)
    consistent = False
    while not consistent:
        consistent = True
        idx_latent_dependencies =\
            [i for i, v in enumerate(dependency_dict.values()) if len(v) > 0]
        for idx in idx_latent_dependencies:
            key_idx = [*dependency_dict.keys()][idx]
            for dependency in dependency_dict[key_idx]:
                if key_idx in dependency_dict[dependency]:
                    raise RuntimeError(
                        f"Found circular dependency between {key_idx} and "
                        f"{dependency}!")
                idx_dependency = [*dependency_dict.keys()].index(dependency)
                if idx_dependency > idx:
                    consistent = False
                    tuples = [*dependency_dict.items()]
                    tuples[idx], tuples[idx_dependency] = \
                        tuples[idx_dependency], tuples[idx]
                    dependency_dict = dict(tuples)

    # translate the prior definitions to objects with computing capabilities
    priors = {}
    for prm_name in dependency_dict.keys():
        prior_template = problem.parameters[prm_name].prior
        priors[prior_template.ref_prm] = \
            translate_prior_template(prior_template)

    # translate the general noise model objects into solver specific ones
    noise_models = []
    for noise_model_base in problem.noise_models:
        noise_models.append(translate_noise_model(noise_model_base))

    def only_values(func):
        """
        This function wrapper is required for the Autograd.apply function which
        is returned by translate_forward_model. While the forward model's
        response method takes a dict as input, the Autograd.apply function
        which will essentially take the forward model's response method's place
        requires the numeric values of inp given as positional arguments. This
        pre-processing step is done by this function wrapper.
        """
        def wrapper(inp):
            return func(*inp.values())
        return wrapper

    def translate_forward_model(forward_model):
        """
        Translates a given forward model (based on non-tensor in/outputs) to a
        torch-compatible forward model based on tensors.

        Parameters
        ----------
        forward_model : ForwardModelBase
            The forward model object. Note that this object needs to have a
            jacobian method to make it translatable.
        """

        class Autograd(th.autograd.Function):

            @staticmethod
            def forward(ctx, *values):
                """
                Modified from the th.autograd.Function docstring: This function
                must accept a context ctx as the first argument, followed by any
                number of arguments (tensors or other types). The context can be
                used to store arbitrary data that can be then retrieved during
                the backward pass.

                Parameters
                ----------
                ctx : torch.autograd.function.AutogradBackward
                    This context-object is for derivative-bookkeeping.
                values : iterable[th.tensor]
                    The values of the inp-dict of the inp-argument of the
                    forward model's 'response' method in the same order.
                """

                # the idea is that the given forward model only takes numeric
                # (non-tensor) inputs; so there needs to be a conversion which
                # takes place here; also, the given values must be rearranged
                # in the dict-format required by forward_model's response method
                inp = {}
                keys = [isens.name for isens in forward_model.input_sensors] +\
                       [*forward_model.prms_def.values()]
                for key, value in zip(keys, values):
                    if th.is_tensor(value):
                        inp[key] = value.detach().numpy()
                    else:
                        inp[key] = value

                # evaluate the forward model and its jacobian for the given
                # input parameters; this is where the forward model is evaluated
                # in its original setup without any tensors
                response_dict = forward_model.response(inp)
                jac_dict = forward_model.jacobian(inp)
                jac_numpy = forward_model.jacobian_dict_to_array(inp, jac_dict)

                # now we have the forward model's response in dict format;
                # however, in this format it cannot be processed here, so we
                # will put all its numeric results in one long vector
                n1, _, n3 = jac_numpy.shape
                response_numpy = np.zeros(n1 * n3)
                response_structure = dict()
                for i, (key, value) in enumerate(response_dict.items()):
                    n_elements = len_or_one(value)
                    idx_start = i * n3
                    idx_end = idx_start + n_elements
                    response_numpy[idx_start: idx_end] = value
                    response_structure[key] = n_elements
                forward_model.response_structure = response_structure

                # now, we need to translate back to the tensor-world
                jac_torch = th.from_numpy(jac_numpy)
                y_torch = th.from_numpy(response_numpy)

                # the jacobian is recorded, and the model evaluation returned
                ctx.save_for_backward(jac_torch)
                return y_torch

            @staticmethod
            def backward(ctx, *dl_dy_tuple):
                """
                From the torch.autograd.Function docstring (modified): It must
                accept a context object as the first argument (ctx), followed by
                as many outputs as the 'forward' method of this class returned
                (None will be passed for non-tensor outputs of the 'forward'
                method), and it should return as many tensors, as there were
                inputs to 'forward' method. Each argument of this method (i.e.,
                the 'backward' method) is the gradient w.r.t the given output,
                and each returned value should be the gradient w.r.t. the
                corresponding input. If an input is either not a Tensor or a
                Tensor not requiring gradients, you can just pass None as a
                gradient for that particular input.

                Parameters
                ----------
                ctx : torch.autograd.function.AutogradBackward
                    This context-object is for derivative-bookkeeping.
                dl_dy_tuple : tuple(torch.Tensor)
                    The jacobian of the likelihood with respect to the forward
                    model evaluations, i.e., dl/dy. Since the likelihood returns
                    a scalar value, this is a tuple of length 1.

                Returns
                -------
                tuple
                    Each element i of the tuple is a tensor that represents the
                    derivative dl/dtheta_i. The order must correspond to the
                    order in the input arguments of the forward-method after the
                    ctx argument. An element is None, if it does not influence
                    the value of the likelihood function.
                """

                # this is dy/dtheta (the gradient with respect to the forward
                # model evaluation) stored from the forward pass; note that the
                # '[0]' at the end is necessary since ctx.saved_tensors is a
                # tuple of all saved tensors; here, there is only one element
                # in it, but we still need to request it
                dy_dtheta = ctx.saved_tensors[0]

                # this is dl/dtheta (the gradient of the likelihood function
                # with respect to the model parameters theta); note that
                # dl_dy_tuple is a tuple of length 1 since the likelihood is a
                # scalar (and not a vector-valued) function
                shape = dy_dtheta.size()
                dl_dy = th.reshape(dl_dy_tuple[0], (shape[0], 1, shape[2]))

                # now dl/dtheta (grad) is computed using the chain rule (which
                # reads dl/dy * dy/dtheta here); the transposition is required
                # so that the matrix multiplication is consistent
                dy_dtheta_transposed = th.transpose(dy_dtheta, 1, 2)
                grad_total = th.matmul(dl_dy, dy_dtheta_transposed)

                # at this point, grad_total will have the shape (n, 1, m) where
                # n is the number of the forward model's output sensors and m is
                # the number of the forward model's input channels; each of the
                # (1, m) sub-tensors can be interpreted as the total gradient
                # dl/dtheta if the forward model had only the corresponding one
                # output sensor; but since it has n output sensors one has to
                # sum over all of those to get the total gradient
                grad_total = th.sum(grad_total, dim=0)

                # the returned gradients must be in tuple form; note that
                # ctx.needs_input_grad is a list with True/False elements of
                # the same lengths as there are parameters in theta; elements
                # in theta that are not being sampled (for example the value of
                # an input sensor of the forward model) are not required to have
                # their gradients evaluated, so these elements will have 'False'
                # entries in ctx.needs_input_grad
                return_val = tuple(
                    None if not required else grad for required, grad
                    in zip(ctx.needs_input_grad, th.flatten(grad_total)))
                return return_val

        return only_values(Autograd.apply)

    # translate the problem's forward models into torch compatible ones
    for fwd_model_name in problem.forward_models.keys():
        setattr(problem.forward_models[fwd_model_name], 'call',
                translate_forward_model(problem.forward_models[fwd_model_name]))

    def get_theta_samples():
        """
        Provides a list of latent-parameter samples in form of torch.Tensors.

        Returns
        -------
        list[torch.Tensor]
            The sampled values based on the latent parameter's priors.
        """
        # even if a list is returned by this function, we initialize a dict here
        # to enable the action in the if-statement in the loop below
        pyro_parameter_samples = dict()
        # we have to sample each parameter for which a prior is defined
        for ref_prm, prior_obj in priors.items():
            hyperprms_dict = {}
            for name in prior_obj.hyperparameters.keys():
                if name in pyro_parameter_samples:
                    hyperprms_dict[name] = pyro_parameter_samples[name]
                else:
                    # this is the default case, where the prior's parameters
                    # (i.e. the hyperparameters) are simply constants
                    hyperprms_dict[name] = problem.parameters[name].value
            # this is where the parameter's sample is generated with pyro
            pyro_parameter_samples[ref_prm] = prior_obj.sample(hyperprms_dict)
        return [*pyro_parameter_samples.values()]

    def loglike(theta):
        """
        Evaluates the log-likelihood function of the problem at theta.

        Parameters
        ----------
        theta : list[torch.Tensor]
            A vector of pyro.samples (i.e. tensors) for which the log-likelihood
            function should be evaluated.

        Returns
        -------
        ll : torch.Tensor
            The evaluated log-likelihood function for the given theta-vector.
        """
        # compute the contribution to the log-likelihood function for each noise
        # model and sum it all up
        for noise_model in noise_models:
            # compute the model response for the noise model's experiment_names
            model_response = problem.evaluate_model_response(
                theta, noise_model.experiment_names)
            # get the tensors for the noise model's parameters
            prms_noise = problem.get_parameters(theta, noise_model.prms_def)
            # evaluate the loglike-contribution for the noise model
            noise_model.sample_cond_likelihood(model_response, prms_noise)

    def posterior_model():
        """
        Returns the sampled log-likelihood in form of a torch.Tensor.

        Returns
        -------
        list[torch.Tensor]
            The sampled values based on the latent parameter's priors.
        """
        theta = get_theta_samples()
        return loglike(theta)

    # perform the sampling with the requested parameters
    kernel = NUTS(posterior_model, step_size=step_size, jit_compile=False)
    mcmc = MCMC(kernel, num_samples=n_steps, warmup_steps=n_initial_steps,
                num_chains=n_walkers, disable_progbar=not verbose)
    mcmc.run()

    # translate the results to a common data structure and return it
    var_names = problem.get_theta_names(tex=False)
    var_names_tex = problem.get_theta_names(tex=True)
    name_dict = {var_name: var_name_tex for var_name, var_name_tex
                 in zip(var_names, var_names_tex)}
    inference_data = az.from_pyro(mcmc)
    inference_data.rename(name_dict, groups='posterior', inplace=True)

    return inference_data