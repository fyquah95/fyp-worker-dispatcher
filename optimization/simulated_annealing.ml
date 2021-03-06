open Core
open Async

module Common = struct
  type config = Simulated_annealing_intf.config =
    { t_max             : float;
      t_min             : float;
      updates           : int;
      steps             : int;
      workers           : int;
    }
  [@@deriving sexp_of]

  let temperature (config : config) step =
    let step = Float.of_int step in
    let steps = Float.of_int config.steps in
    let t_factor = -1.0 *. Float.log (config.t_max /. config.t_min) in
    config.t_max *. Float.exp (t_factor *. step /. steps)
  ;;
end

let default_config =
  { Common.
    t_max = 0.04;
    t_min = 0.001;
    updates = 100;
    steps = 300;
    workers = 3;
  }

module Make(T: Simulated_annealing_intf.T) = struct

  type t =
    { state          : T.state;
      current_energy : T.energy;
      step           : int;
      accepts        : int;
      improves       : int;

      energy_cache   : T.energy Deferred.t T.Map.t;
      best_solution  : T.state * T.energy;

      config         : Common.config;
    }
  [@@deriving sexp_of]

  let sexp_of_t t =
    sexp_of_t { t with energy_cache = T.Map.empty }
  ;;

  module Step = struct
    type t = (T.state, T.energy) Simulated_annealing_intf.Step.t
    [@@deriving sexp]
  end

  let empty ?(config = default_config) state current_energy =
    { state;
      current_energy;
      step = 0;
      accepts = 0;
      improves = 0;

      energy_cache = T.Map.empty;
      best_solution = (state, current_energy);

      config;
    }

  let query_energy t s =
    match T.Map.find t.energy_cache s with
    | None   ->
      Log.Global.sexp ~level:`Info
        [%message "Computing energy for state "
           (s : T.state)];
      (* TODO: ok_exn is NOT okay *)
      let deferred_energy = T.energy s >>| ok_exn in
      let energy_cache =
        T.Map.update t.energy_cache s ~f:(fun _ -> deferred_energy)
      in
      { t with energy_cache }, deferred_energy
    | Some deferred_energy ->
      Log.Global.sexp ~level:`Info
        [%message "Loading energy from cache for state"
           (s : T.state)];
      t, deferred_energy
  ;;

  let bump_best_solution t state energy =
    let (best_soln, best_energy) = t.best_solution in
    if T.energy_to_float energy <. T.energy_to_float best_energy
    then { t with best_solution = state, energy }
    else t
  ;;

  let step t =
    let t_step = t.step in
    Log.Global.sexp ~level:`Info
      [%message (t_step : int) "Calling step" (t : t)];
    let temperature = Common.temperature t.config t.step in
    let current_state = t.state in
    let current_energy = t.current_energy in
    assert (t.config.workers >= 1);
    (* TODO: ok_exn here is NOT okay! *)
    let%bind candidate_next_states =
      Deferred.List.init t.config.workers ~how:`Sequential ~f:(fun worker_id ->
        Deferred.repeat_until_finished 0 (fun ctr ->
          T.move ~config:t.config ~step:t.step ~sub_id:worker_id current_state
          >>| function
          | Ok x -> `Finished x
          | Error e ->
            if ctr >= 10 then
              failwithf
                !"SA failed to move after 10 steps. Terminating %{Error.to_string_hum}"
                e ()
            else begin
              Log.Global.info
                !"SA failed to move after %d steps with error %{Error.to_string_hum}"
                ctr e;
              `Repeat (ctr + 1)
            end))
    in
    let%bind (t, next_state, next_energy) =
      let t, candidate_next_states_with_deferred_energy =
        List.foldi ~init:(t, []) candidate_next_states
          ~f:(fun worker_id (t, acc) state ->
            let t, deferred_energy = query_energy t state in
            t, ((state, deferred_energy) :: acc))
      in
      Deferred.List.map candidate_next_states_with_deferred_energy
        ~how:`Parallel
        ~f:(fun (candidate_state, deferred_energy) ->
          deferred_energy >>| fun energy -> (candidate_state, energy))
      >>| fun candidates ->
      let cmp (_, a) (_, b) =
        Float.compare (T.energy_to_float a) (T.energy_to_float b)
      in
      let next_state, next_energy =
        Option.value_exn (List.min_elt candidates ~compare:cmp)
      in
      t, next_state, next_energy
    in
    let t = bump_best_solution t current_state current_energy in
    let t = bump_best_solution t next_state next_energy in
    let d_e =
      T.energy_to_float next_energy -. T.energy_to_float current_energy
    in
    let rand = Random.float 1.0 in
    let probability =
      if d_e <= 0.0 then 1.0
      else Float.exp (Float.neg d_e /. temperature)
    in
    let step = t.step in
    Log.Global.sexp ~level:`Info
      [%message
        (step : int)
        (probability : float)
        (rand : float)
        (current_energy : T.energy)
        (next_energy : T.energy)];
    if d_e > 0.0 && probability <. rand then begin
      Log.Global.sexp ~level:`Info [%message (step : int) "Rejecting change!"];
      let step =
        { Simulated_annealing_intf.Step.
          initial  = (current_state, current_energy);
          proposal = (next_state, next_energy);
          step     = t.step;
          decision = `Rejected
        }
      in
      return (step, { t with step = t.step + 1 })
    end else begin
      let accepts = t.accepts + 1 in
      let improves = if d_e <. 0.0 then t.improves + 1 else t.improves in
      let step_description =
        { Simulated_annealing_intf.Step.
          initial  = (current_state, current_energy);
          proposal = (next_state, next_energy);
          step     = t.step;
          decision = `Accepted
        }
      in
      let state = next_state in
      Log.Global.sexp ~level:`Info
        [%message (step: int) "Accepting change to new state!"];
      return (
        step_description,
        { t with state; step = t.step + 1; accepts; improves;
                      current_energy = next_energy; })
    end
  ;;
end
