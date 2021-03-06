open Core
open Async
open Common

(* The availble algorithms in RL are as follows:
 *
 * MCTS_WITH_IRL
 *   policy <- initial_sensible_policy
 *
 *   loop until timeout {
 *     trajectories <- sample N trajectories with MCTS using policy
 *     learn reward function using trajectories (basically, do IRL)
 *     policy <- solve optimal policy using reward function (with simple DP)
 *   }
 *
 *   result <- argmax(policy)
 *
 * Asynchronous MCTS
 *   MCTS as described in AlphaGo's paper, but without using value or policy
 *   networks
 *)

module RL = Rl  (* RL is much nicer alias than Rl :) *)
module Cfg = Cfg
module EU = Experiment_utils
module Async_MCTS = Async_mcts


let command_run =
  let open Command.Let_syntax in
  Command.async_or_error ~summary:"Command"
    [%map_open
      let { Command_params.
        config_filename; controller_rundir; exp_dir; bin_name; bin_args;
        module_paths; round;
        bin_files;
      } = Command_params.params
      and log_rewards =
        flag "-log-rewards" (required bool) ~doc:"BOOL logarithmic speedups as reward"
      and num_iterations =
        flag "-num-iterations" (required int) ~doc:"INT Number of iterations"
      in
      fun () ->
        Reader.load_sexp config_filename [%of_sexp: Config.t]
        >>=? fun config ->
        Log.Global.sexp ~level:`Info [%message
          "Initializing Worker connections!"
            (config: Config.t)];
        Deferred.Or_error.List.map config.worker_configs ~how:`Parallel
          ~f:(fun worker_config ->
            let hostname = Protocol.Config.hostname worker_config in
            Experiment_utils.init_connection ~hostname ~worker_config)
        >>=? fun worker_connections ->
        let env = [("OCAMLPARAM", "_,exhaustive-inlining=1")] in

        Log.Global.sexp ~level:`Info [%message
          "Initializing Worker connections!"
            (config: Config.t)];
        Experiment_utils.get_initial_state ~env ~bin_name ~exp_dir ~round
            ~module_paths ~base_overrides:[] ()
        >>=? fun initial_state ->

        let initial_state = Option.value_exn initial_state in
        let decisions = initial_state.v1_decisions in

        Log.Global.info "Constructing inlining tree";
        let inlining_tree = Inlining_tree.V1.build decisions in

        Log.Global.info "Constructing CFG";
        let cfg = Cfg.t_of_inlining_tree inlining_tree in

        Log.Global.info "Saving CFG and inlining tree to rundir";
        lift_deferred (
          Writer.save_sexp (controller_rundir ^/ "cfg.sexp")
            ([%sexp_of: Cfg.t] cfg)
        )
        >>=? fun () ->
        lift_deferred (
          Writer.save_sexp (controller_rundir ^/ "inlining_tree.v1.sexp")
            ([%sexp_of: Inlining_tree.V1.Top_level.t] inlining_tree))
        >>=? fun () ->

        (* It is okay to run just twice (or even once?), I believe.
         *
         * MCTS should be smart enough to retry due to sampling errors.
         * *)
        let process =
          Experiment_utils.process_work_unit ~num_runs:config.num_runs
            ~bin_args ~bin_files
        in
        lift_deferred (EU.Scheduler.create worker_connections ~process)
        >>=? fun scheduler ->
        let dump_directory =
          Experiment_utils.Dump_utils.execution_dump_directory
            ~step:`Initial ~sub_id:`Current
        in
        EU.compile_binary ~dir:exp_dir ~bin_name:bin_name
          ~write_overrides:(fun _ -> Deferred.Or_error.ok_unit)
          ~dump_directory
        >>=? fun basic_path_to_bin ->

        EU.run_in_all_workers ~times:3 ~scheduler ~config ~path_to_bin:basic_path_to_bin
        >>=? fun initial_execution_stats ->

        let baseline_exec_time = gmean_exec_time initial_execution_stats in
        let reward_of_exec_time (time : Time.Span.t) =
          (* The relative difference in performance *)
          if log_rewards then
            let ratio =
              Time.Span.to_sec time /. Time.Span.to_sec baseline_exec_time
            in
            (-1.0) *. (Float.log ratio)
          else
            let baseline = Time.Span.to_sec baseline_exec_time in
            let diff = baseline -. Time.Span.to_sec time in
            diff /. baseline
        in
        let compile_binary =
          (* Lock required because we don't want to processes to be
           * compiling code at the same time -- it'd cause a recase in
           * the available data structures
           *)
          let lock = Nano_mutex.create () in
          fun (`Iter_id iter_id) (`Incomplete partial_trajectory) ->
            With_lock.run lock ~name:(sprintf "Iter: %d" iter_id)(fun () ->
              let write_overrides filename =
                let overrides =
                  Cfg.overrides_of_pending_trajectory cfg partial_trajectory
                in
                lift_deferred (
                  Writer.save_sexp  filename
                    ([%sexp_of: Data_collector.V1.Overrides.t] overrides))
              in
              let dump_directory_for_compilation =
                Experiment_utils.Dump_utils.execution_dump_directory
                  ~step:(`Step iter_id) ~sub_id:`Current
              in
              EU.compile_binary ~dir:exp_dir ~bin_name:bin_name ~write_overrides
                ~dump_directory:dump_directory_for_compilation
              >>= fun filename ->
              let filename = ok_exn filename in
              (EU.read_decisions ~exp_dir ~module_paths ~round >>|? snd)
              >>= fun decisions ->
              let decisions = Or_error.ok_exn decisions in
              let visited_states =
                List.map ~f:fst (fst partial_trajectory)
                |> RL.S.Set.of_list
              in
              let pprint fc =
                let pprint ~apply_id ~(function_metadata : Data_collector.V1.Function_metadata.t) =
                  Format.asprintf "[%a](%a)"
                    Apply_id.print apply_id
                    Closure_origin.print function_metadata.closure_origin
                in
                let trace =
                  List.map (List.rev fc.Cfg.Function_call.inlining_trace)
                    ~f:(fun (apply_id, function_metadata) -> pprint ~apply_id ~function_metadata)
                in
                trace @ [pprint ~apply_id:fc.apply_id ~function_metadata:fc.applied]
                |> String.concat ~sep:" -(calls)-> "
              in
              let remaining_trajectory =
                List.filter_map decisions ~f:(fun decision ->
                    Option.bind (Cfg.Function_call.t_of_decision decision)
                      ~f:(fun fc ->
                          match Cfg.Function_call.Map.find cfg.reverse_map fc with
                          | None ->
                            Log.Global.info "FAILED TO MATCH PATH:        %s"
                              (pprint fc);
                            None
                          | Some s ->
                            if RL.S.Set.mem visited_states s then
                              None
                            else
                              Some (s, decision.action)))
                |> List.sort ~compare:(fun a b -> RL.S.compare (fst a) (fst b))
              in
              let pending_trajectory =
                (fst partial_trajectory @ remaining_trajectory, RL.S.terminal)
              in
              Deferred.return (filename, pending_trajectory))
            >>| fun (a : (string * ((RL.S.t * Rl.A.t) list * RL.S.t))) ->
            Or_error.return a
        in
        let execute_work_unit work_unit =
          EU.Scheduler.dispatch scheduler work_unit
        in
        let transition state action =
          let next = Cfg.transition cfg state action in
          next
        in
        Async_mcts.learn ~parallelism:(List.length worker_connections)
          ~num_iterations:num_iterations
          ~root_state:cfg.root
          ~transition
          ~compile_binary
          ~execute_work_unit
          ~reward_of_exec_time
    ]
;;

let command = Command.group ~summary:"" [("run", command_run)]
