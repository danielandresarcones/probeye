# standard library
import unittest

# third party imports
import numpy as np

# local imports
from probeye.definition.forward_model import ForwardModelBase
from probeye.definition.distribution import Normal, Uniform
from probeye.definition.sensor import Sensor
from probeye.definition.inverse_problem import InverseProblem
from probeye.definition.likelihood_model import GaussianLikelihoodModel
from probeye.inference.emcee.solver import EmceeSolver


class TestProblem(unittest.TestCase):
    def test_emcee_solver(self):

        # define the forward model
        class LinRe(ForwardModelBase):
            def interface(self):
                self.parameters = ["a", "b"]
                self.input_sensors = Sensor("x")
                self.output_sensors = Sensor("y", std_model="sigma")

            def __call__(self, inp):
                x = inp["x"]
                a = inp["a"]
                b = inp["b"]
                return {"y": a * x + b}

        # set up the problem
        problem = InverseProblem("Linear regression")
        problem.add_parameter("a", prior=Normal(mean=0, std=1))
        problem.add_parameter("b", prior=Normal(mean=0, std=1))
        problem.add_parameter("sigma", prior=Uniform(low=0.1, high=1))

        # generate and add some simple test data
        n_tests, a_true, b_true, sigma_true, seed = 5000, 0.3, -0.2, 0.1, 6174
        np.random.seed(seed)
        x_test = np.linspace(0.0, 1.0, n_tests)
        y_true = a_true * x_test + b_true
        y_test = np.random.normal(loc=y_true, scale=sigma_true)
        problem.add_experiment("Tests", sensor_data={"x": x_test, "y": y_test})

        # add the forward model
        problem.add_forward_model(LinRe("LinRe"), experiments="Tests")

        # add the likelihood model
        problem.add_likelihood_model(
            GaussianLikelihoodModel(experiment_name="Tests", model_error="additive")
        )

        # run the emcee solver with different seeds
        n_walkers, n_steps = 10, 100

        emcee_solver_1a = EmceeSolver(problem, seed=123)
        inference_data_1a = emcee_solver_1a.run(n_walkers=n_walkers, n_steps=n_steps)

        emcee_solver_2 = EmceeSolver(problem, seed=42)
        inference_data_2 = emcee_solver_2.run(n_walkers=n_walkers, n_steps=n_steps)

        emcee_solver_1b = EmceeSolver(problem, seed=123)
        inference_data_1b = emcee_solver_1b.run(n_walkers=n_walkers, n_steps=n_steps)

        # first, check that the sampled results make sense
        true_values = {"a": a_true, "b": b_true, "sigma": sigma_true}
        for prm_name, mean_true in true_values.items():
            mean = emcee_solver_2.summary["mean"][prm_name]
            self.assertAlmostEqual(mean, mean_true, delta=0.01)

        # check that the results of '1a' and '2' are not similar (different seeds)
        same_results = True
        for prm_name in ["a", "b", "sigma"]:
            v1 = inference_data_1a["posterior"][prm_name].values.flatten()
            v2 = inference_data_2["posterior"][prm_name].values.flatten()
            if np.alltrue(v1 != v2):
                same_results = False
                break
        self.assertTrue(not same_results)

        # check that the results of '1a' and '1b' are similar (same seeds)
        same_results = True
        for prm_name in ["a", "b", "sigma"]:
            v1 = inference_data_1a["posterior"][prm_name].values.flatten()
            v2 = inference_data_1b["posterior"][prm_name].values.flatten()
            if np.alltrue(v1 != v2):
                same_results = False
                break
        self.assertTrue(same_results)


if __name__ == "__main__":
    unittest.main()
