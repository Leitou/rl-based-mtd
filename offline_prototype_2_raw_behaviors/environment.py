from typing import Dict, Tuple, List
from collections import defaultdict
from custom_types import Behavior, MTDTechnique, actions, supervisor_map
from autoencoder import AutoEncoderInterpreter
from scipy import stats
from tabulate import tabulate
import torch
import numpy as np
import pandas as pd
import os
import random



class SensorEnvironment:

    def __init__(self, train_data: Dict[Behavior, np.ndarray] = None,
                 interpreter: AutoEncoderInterpreter = None, state_samples=1, normal_prob=0.8):
        self.num_state_samples = state_samples
        self.train_data = train_data
        self.normal_prob = normal_prob
        self.current_state: np.array = None
        self.observation_space_size: int = len(self.train_data[Behavior.RANSOMWARE_POC][0][:-1])
        self.actions: List[int] = [i for i in range(len(actions))]
        self.interpreter = interpreter
        self.reset_to_behavior = None

    def sample_initial_decision_state(self):
        """i.e. for starting state of an episode,
        (with replacement; it is possible that the same sample is chosen multiple times)"""
        if np.random.random_sample() < self.normal_prob:
            attack_data = self.train_data[Behavior.NORMAL]
        else:
            rb = random.choice([b for b in Behavior if b != Behavior.NORMAL])
            attack_data = self.train_data[rb]
        return attack_data[np.random.randint(attack_data.shape[0], size=self.num_state_samples), :]

    def sample_behavior(self, b: Behavior):
        behavior_data = self.train_data[b]
        return behavior_data[np.random.randint(behavior_data.shape[0], size=self.num_state_samples), :]

    def step(self, action: int):

        current_behavior = self.current_state[0, -1]

        if current_behavior in supervisor_map[action]:
            # print("correct mtd chosen according to supervisor")
            new_state = self.sample_behavior(Behavior.NORMAL)
            # ae predicts too many false positives: episode should not end, but behavior is normal (because MTD was correct)
            # note that this should not happen, as ae should learn to recognize normal behavior with near perfect accuracy

            # False Positive
            if torch.sum(self.interpreter.predict(new_state[:, :-1].astype(np.float32))) / len(new_state) > 0.5:
                # raise UserWarning("Should not happen! AE fails to predict majority of normal samples")
                reward = self.calculate_reward(False)
                isTerminalState = False
            # True Negative
            else:
                reward = self.calculate_reward(True)
                isTerminalState = True

        else:
            # print("incorrect mtd chosen according to supervisor")
            new_state = self.sample_behavior(current_behavior)
            # if self.num_state_samples > 1:
            #     for i in range(self.num_state_samples - 1):  # real world simulation with multiple samples monitored
            #         new_state = np.vstack((new_state, self.sample_behavior(current_behavior)))
            # False Negative
            # ae predicts a false negative: episode should end,  but behavior is not normal (because MTD was incorrect)
            # in this case, the next episode should start again with current_behavior
            if torch.sum(self.interpreter.predict(new_state[:, :-1].astype(np.float32))) / len(new_state) < 0.5:
                self.reset_to_behavior = current_behavior
                reward = self.calculate_reward(True)
                isTerminalState = True
            # True Positive
            else:
                reward = self.calculate_reward(False)
                isTerminalState = False

        self.current_state = new_state
        if self.num_state_samples > 1:
            new_state = np.expand_dims(new_state[0, :],
                                       axis=0)  # throw away all but one transition for better decorrelation

        return new_state, reward, isTerminalState

    def reset(self):
        while True:
            # in case of wrongful termination of an episode due to a false negative,
            # next episode should start with the given behavior again
            if self.reset_to_behavior:
                print(f"Resetting to behavior: {self.reset_to_behavior}")
                self.current_state = self.sample_behavior(self.reset_to_behavior)
                # WARNING:
                # if the behavior to reset to is never detected as an anomaly,
                # it could get stuck in an endless loop here
            else:
                self.current_state = self.sample_initial_decision_state()

            b = self.current_state[0, -1]

            if (torch.sum(self.interpreter.predict(self.current_state[:, :-1].astype(np.float32))) / len(
                    self.current_state) > 0.5):
                # FP/TP - start training
                # below must be here, otherwise it's possible that there is a false negative and the next episode starts with a different behavior
                self.reset_to_behavior = None
                break

        return np.expand_dims(self.current_state[0, :], axis=0)

    # TODO: possibly adapt to distinguish between MTDs that are particularly wasteful in case of wrong deployment
    def calculate_reward(self, success):
        """this method can be refined to distinguish particularly wasteful/beneficial mtds"""
        if success:
            return 1
        else:
            return -1
