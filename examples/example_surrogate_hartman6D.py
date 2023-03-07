"""
Example of a Bayesian parameter estimation problem using surrogate
modeling with probeye.

The Hartmann test function f:[0, 1]^6 -> R^1 is used to simulate a
physical model. The last two dimensions are considered as space and
time coordinates, while the first four dimensions are taken as
latent variables to be inferred. Measurements are generated by
adding I.i.d. Gaussian noise to samples from this function.

Notes:
    * A specific version of `harlow` is required to run this example, which can
    be found here:
    https://github.com/TNO/harlow/tree/implement-data-container
"""

# =========================================================================
# Imports
# =========================================================================

# third party imports
import numpy as np
import os, sys, inspect

currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, parentdir)

# local imports (problem definition)
from probeye.definition.inverse_problem import InverseProblem
from probeye.definition.forward_model import ForwardModelBase
from probeye.definition.sensor import Sensor
from probeye.definition.likelihood_model import GaussianLikelihoodModel
from probeye.inference.emcee.solver import EmceeSolver
from probeye.definition.distribution import Uniform
from probeye.metamodeling.sampling import LatinHypercubeSampler, HarlowSampler
from probeye.metamodeling.surrogating import HarlowSurrogate

# local imports (inference data post-processing)
from probeye.postprocessing.sampling_plots import create_pair_plot
from probeye.postprocessing.sampling_plots import create_posterior_plot

# Surrogate model imports
from harlow.sampling import FuzzyLolaVoronoi, ProbabilisticSampler, LatinHypercube
from harlow.surrogating import (
    VanillaGaussianProcess,
)
from harlow.utils.transforms import (
    Identity,
)

import torch
from botorch.test_functions.synthetic import Hartmann
from matplotlib import pyplot as plt

# =========================================================================
# General settings
# =========================================================================

plot = True

# Emcee settings
n_steps = 1_000
n_init_steps = 200
n_walkers = 20

# Sampler settings
n_init = 100
n_iter = 5
n_point_per_iter = 20
stopping_criterium = -np.inf

# Surrogate settings
N_train_iter = 50
show_progress = True

# =========================================================================
# Define parameters
# =========================================================================

# Ground truth
X_true = np.array([0.5, 0.5, 0.5, 0.5])

# Bounds for function defined on unit hypercube
X_low = 0.0
X_high = 1.0

# Ground truth and prior for measurement uncertainty std. dev.
std_true = 0.05
std_low = 0.0
std_high = 1.0

# =========================================================================
# Define physical model
# =========================================================================

# Number of sensors and number of points in timeseries
Nx = 3
Nt = 5

# Sensor names and positions
sensor_names = ["S" + str(i + 1) for i in range(Nx)]
x_vec = np.linspace(0, 1, Nx)
t_vec = np.linspace(0, 1, Nt)

isensor = Sensor("t")
osensor_list = [
    Sensor(sensor_names[i], x=float(x_vec[i]), std_model="sigma") for i in range(Nx)
]

# =========================================================================
# Define forward model
# =========================================================================

# Initialize model
expensive_model = Hartmann(noise_std=0.00001)


class SyntheticModel(ForwardModelBase):
    def interface(self):
        self.parameters = ["X" + str(i + 1) for i in range(4)]
        self.input_sensors = isensor
        self.output_sensors = osensor_list

    def response(self, inp: dict) -> dict:

        # Arange input vector
        params = np.tile([inp["X" + str(i + 1)] for i in range(4)], (Nx * Nt, 1))
        xt = np.array(np.meshgrid(x_vec, t_vec)).T.reshape(-1, 2)
        X = torch.tensor(np.hstack((params, xt)))

        # Evaluate function and arange output on grid
        f = np.array(expensive_model(X)).reshape(Nx, Nt)

        # Store prediction as dict
        response = dict()
        for idx_x, os in enumerate(self.output_sensors):
            response[os.name] = f[idx_x, :]
        return response


# =========================================================================
# Define inference problem
# =========================================================================
problem = InverseProblem("Parameter estimation using surrogate model")

# Parameters of the Hartmann function
for i in range(4):
    problem.add_parameter(
        "X" + str(i + 1),
        "model",
        prior=Uniform(low=X_low, high=X_high),
        info="Parameter of the 6D Hartmann function",
        tex=r"$X_{{{}}}$".format(i + 1),
    )

# Noise std. dev.
problem.add_parameter(
    "sigma",
    "likelihood",
    prior=Uniform(low=std_low, high=std_high),
    info="Std. dev. of zero-mean noise model",
    tex=r"$\sigma$",
)

# add the forward model to the problem
forward_model = SyntheticModel("ExpensiveModel")

# =========================================================================
# Add test data to the inference problem
# =========================================================================
def generate_data():
    inp = {"X" + str(idx + 1): X_i for idx, X_i in enumerate(X_true)}
    sensors = forward_model(inp)
    for sname, svals in sensors.items():
        sensors[sname] = list(
            np.array(svals) + np.random.normal(loc=0.0, scale=std_true, size=Nt)
        )

    # To avoid errors when `Nt = 1`
    if len(t_vec) == 1:
        sensors[isensor.name] = float(t_vec[0])
    else:
        sensors[isensor.name] = t_vec

    # Add experiments
    problem.add_experiment("TestSeriesFull", sensor_data=sensors)
    problem.add_experiment("TestSeriesSurrogate", sensor_data=sensors)


# Generate data and add expensive forward model
generate_data()
problem.add_forward_model(forward_model, experiments="TestSeriesFull")

# =========================================================================
# Create surrogate model
# =========================================================================
n_params = 4
list_params = [[i for i in range(n_params)]] * len(sensor_names) * len(t_vec)

# Kwargs to be passed to the surrogate model
surrogate_kwargs = {
    "training_max_iter": N_train_iter,
    "list_params": list_params,
    "show_progress": True,
    "silence_warnings": True,
    "fast_pred_var": True,
    "input_transform": Identity,
    "output_transform": Identity,
}

# Define the surrogate model
surrogate_model = VanillaGaussianProcess(**surrogate_kwargs)

# Probeye's latin hypercube sampler
lhs_sampler = LatinHypercubeSampler(problem)

# An iterative sampler. Here we pass the surrogate ForwardModel directly to the sampler. However, it is
# also possible to pass a surrogate model that will be included in a forward model after fitting.
harlow_sampler = HarlowSampler(problem, forward_model, LatinHypercube, surrogate_model)

# Sampler and fit
harlow_sampler.sample(
    n_initial_points=n_init,
    n_new_points_per_iteration=n_point_per_iter,
    max_n_iterations=n_iter,
    stopping_criterium=stopping_criterium,
)
harlow_sampler.fit()

# Define the surrogate forward model
forward_surrogate_model = HarlowSurrogate(
    "SurrogateModel", surrogate_model, forward_model
)

# =========================================================================
# Add forward models
# =========================================================================

# Add surrogate model to forward models
problem.add_forward_model(forward_surrogate_model, experiments="TestSeriesSurrogate")

# add the likelihood models to the problem
for osensor in osensor_list:
    problem.add_likelihood_model(
        GaussianLikelihoodModel(
            experiment_name="TestSeriesSurrogate",
            model_error="additive",
        )
    )

# ====================================================================
# Plot surrogate vs. FE model prediction
# ====================================================================

# Physical model prediction
arr_x = np.linspace(X_low, X_high, 100)
inp = {"X" + str(idx + 1): X_i for idx, X_i in enumerate(X_true)}

# Initialize zero arrays to store results
y_true = np.zeros((len(arr_x), len(sensor_names) * len(t_vec)))
y_pred = np.zeros((len(arr_x), len(sensor_names) * len(t_vec)))

# Evaluate physical model for each input vector
for idx_xi, xi in enumerate(arr_x):
    inp["X1"] = xi
    res_true = forward_model.response(inp)
    res_pred = forward_surrogate_model.response(inp)
    sensor_out_true = []
    sensor_out_pred = []
    for idx_os, os in enumerate(osensor_list):
        sensor_out_true.append(res_true[os.name])
        sensor_out_pred.append(res_pred[os.name])
    y_true[idx_xi, :] = np.ravel(sensor_out_true)
    y_pred[idx_xi, :] = np.ravel(sensor_out_pred)

# Plot
nrows = 3
ncols = int(np.ceil(len(sensor_names) * len(t_vec) / 3))
f, axes = plt.subplots(nrows, ncols, sharex=True, figsize=(3 * ncols, 3 * nrows))
for j in range(len(sensor_names) * len(t_vec)):
    ax_i = axes.ravel()[j]
    grid_idx = np.unravel_index(j, (nrows, ncols))
    ax_i.plot(arr_x, y_true[:, j], color="blue", label="True")
    ax_i.plot(arr_x, y_pred[:, j], color="red", linestyle="dashed", label="Surrogate")
    ax_i.set_title(
        f"Sensor: {str(sensor_names[grid_idx[0]]) + '_' + str(t_vec[grid_idx[1]])}"
    )

axes = np.atleast_2d(axes)
[ax_i.set_xlabel(r"$X_1$") for ax_i in axes[-1, :]]
axes[0, 0].legend()
plt.show()


# =========================================================================
# Add noise models
# =========================================================================
# Problem overview
problem.info()

true_values = {
    "X1": X_true[0],
    "X2": X_true[1],
    "X3": X_true[2],
    "X4": X_true[3],
    "sigma": std_true,
}

# =========================================================================
# Initialize and run solver
# =========================================================================

solver = EmceeSolver(
    problem,
    show_progress=True,
)


inference_data = solver.run(
    n_walkers=n_walkers, n_steps=n_steps, n_initial_steps=n_init_steps, vectorize=False
)

# =========================================================================
# Plotting
# =========================================================================
create_pair_plot(
    inference_data,
    solver.problem,
    show=False,
    true_values=true_values,
    title="Joint posterior",
)

create_posterior_plot(
    inference_data,
    solver.problem,
    show=False,
    true_values=true_values,
    title="Marginal posteriors",
)


if plot:
    plt.show()  # shows all plots at once due to 'show=False' above
else:
    plt.close("all")
