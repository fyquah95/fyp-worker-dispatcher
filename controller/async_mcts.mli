open Core
open Async
open Common

module RL = Rl
module EU = Experiment_utils

val learn
   : parallelism: int
  -> num_iterations: int
  -> root_state: RL.S.t
  -> transition: RL.transition
  -> compile_binary: (
      [`Incomplete of RL.Pending_trajectory.t]
      -> (string * Rl.Pending_trajectory.t) Deferred.Or_error.t)
  -> execute_work_unit: (EU.Work_unit.t -> Execution_stats.t Deferred.Or_error.t)
  -> reward_of_exec_time: (Time.Span.t -> float)
  -> record_trajectory:(iter: int -> Execution_stats.t RL.Trajectory.t -> unit Deferred.Or_error.t)
  -> unit Deferred.Or_error.t
