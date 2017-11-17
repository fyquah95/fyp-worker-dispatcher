[@@@ocaml.warning "+a-4-9-30-40-41-42-44"]

open Core
open Async
open Common

module Utils = Experiment_utils
module SA = Optimization.Simulated_annealing

module Work_unit = struct
  type t =
    { path_to_bin : string;
      step : int;
      sub_id : int;
    }
  [@@deriving sexp]
end

module Make_annealer(M: sig

  val initial_execution_time : Time.Span.t

  val scheduler :
    (Work_unit.t,
     Execution_stats.t Or_error.t,
     Socket.Address.Inet.t) Utils.Scheduler.t

  val run_dir : string

  val exp_dir : string

  val bin_name : string

end) = struct
  module T = struct
    type energy = Execution_stats.t [@@deriving sexp]

    type state =
      { tree        : Inlining_tree.Top_level.t;
        work_unit   : Work_unit.t;
      }
    [@@deriving sexp]

    type t = state [@@deriving sexp]

    (* We don't want debugging messages to contain the entire tree. *)
    let sexp_of_state state = Work_unit.sexp_of_t state.work_unit

    let energy_to_float (e : energy) =
      let a =
        geometric_mean (List.map e.raw_execution_time ~f:Time.Span.to_sec)
      in
      a /. Time.Span.to_sec M.initial_execution_time
    ;;

    let compare a b = List.compare Inlining_tree.compare a.tree b.tree
  end

  module T2 = struct
    include T
    include Comparable.Make(T)

    let unique_random_from_list ~count choices =
      let rec loop ~choices ~(left : int) =
        if Int.O.(left <= 0) then []
        else begin
          let size = Int.Set.length choices in
          let selected =
            Option.value_exn (Int.Set.nth choices (Random.int size))
          in
          selected :: loop ~left:(left - 1) ~choices:(Int.Set.remove choices selected)
        end
      in
      loop ~choices:(Int.Set.of_list choices) ~left:count
    ;;

    let step_table = Int.Table.create ()

    let get_sub_id step =
      let r = ref 0 in
      begin
      Int.Table.update step_table step ~f:(function
          | None -> r := 0; 1
          | Some x -> r := x; x + 1)
      end;
      !r
    ;;

    let dump_directory_name ~step ~sub_id=
      let step =
        match step with
        | -1 -> "initial"
        | x -> Int.to_string x
      in
      let sub_id =
        match sub_id with
        | -1 -> "current"
        | x -> Int.to_string x
      in
      M.run_dir ^/ "opt_data" ^/ step ^/ sub_id
    ;;

    let move ~(step : int) ~(config: SA.Common.config) state =
      ignore step;
      ignore config;
      let current_tree = state.tree in
      let num_leaves = Inlining_tree.Top_level.count_leaves current_tree in
      (* flip between 1 to 3 leaves *)
      let new_tree =
        let rec make () =
          if Random.bool () then
            let modified_leaves = Int.min num_leaves (Random.int 3 + 1) in
            let choices =
              unique_random_from_list ~count:modified_leaves
                (List.init num_leaves ~f:Fn.id)
            in
            Inlining_tree.Top_level.flip_several_leaves current_tree choices
          else
            let leaf = Random.int num_leaves in
            match
              Inlining_tree.Top_level.backtrack_nth_leaf current_tree leaf
            with
            | None ->
              Log.Global.sexp ~level:`Info [%message "Failed to backtrack!"];
              make ()
            | Some transformed ->
              Log.Global.sexp ~level:`Info [%message "Back track possible!!"];
              transformed
        in
        make ()
      in
      shell ~dir:M.exp_dir "make" [ "clean" ]
      >>=? fun () ->
      let overrides = Inlining_tree.Top_level.to_override_rules new_tree in
      lift_deferred (
        Writer.save_sexp (M.exp_dir ^/ "overrides.sexp")
          ([%sexp_of: Data_collector.t list] overrides)
      )
      >>=? fun () ->
      shell ~dir:M.exp_dir "make" [ "all" ]
      >>=? fun () ->
      let filename = Filename.temp_file "fyp-" ("-" ^ M.bin_name) in
      shell ~dir:M.exp_dir
        "cp" [ (M.bin_name ^ ".native"); filename ]
      >>=? fun () ->
      shell ~dir:M.exp_dir "chmod" [ "755"; filename ]
      >>=? fun () ->
      let data_collector_file =
        M.exp_dir ^/ (M.bin_name ^ ".0.data_collector.sexp")
      in
      Reader.load_sexp data_collector_file [%of_sexp: Data_collector.t list]
      >>=? fun executed_decisions ->

      let sub_id = get_sub_id step in
      let dump_directory = dump_directory_name ~step ~sub_id in
      let copy_with_wildcard src dest =
        shell ~dir:M.exp_dir "bash" [ "-c"; sprintf "cp -f %s %s" src dest ]
      in
      lift_deferred (Async_shell.mkdir ~p:() dump_directory)
      >>=? fun () -> copy_with_wildcard data_collector_file dump_directory
      >>=? fun () -> copy_with_wildcard (M.exp_dir ^/ "*.s") dump_directory
      >>=? fun () -> copy_with_wildcard (M.exp_dir ^/ "flambda.out") dump_directory
      >>=? fun () ->
      (* TODO: This is incredibly expensive -- there is a lot of potential
       * for tree structure sharing here.
       *)
      let tree = Inlining_tree.build executed_decisions in
      let work_unit = { Work_unit. path_to_bin = filename; step; sub_id } in
      Deferred.Or_error.return { T. tree; work_unit; }
    ;;

    let energy state =
      let num_workers = Experiment_utils.Scheduler.num_workers M.scheduler in
      Deferred.Or_error.List.init num_workers ~how:`Parallel ~f:(fun _ ->
        Experiment_utils.Scheduler.dispatch M.scheduler state.work_unit)
      >>|? fun results ->
      let raw_execution_time =
        List.concat_map results ~f:(fun r -> r.raw_execution_time)
      in
      (* One would imagine that the GC stats are the same across all runs *)
      let gc_stats = (List.hd_exn results).gc_stats in
      let parsed_gc_stats = None in
      let worker_hostname = None in
      { Execution_stats.
        raw_execution_time;
        worker_hostname;
        gc_stats;
        parsed_gc_stats;
      }
    ;;
  end

  include SA.Make(T2)

  let step t =
    let dump_directory = T2.dump_directory_name ~step:t.step ~sub_id:(-1) in
    let%bind () = Async_shell.mkdir ~p:() dump_directory in
    let%bind () =
      Writer.save_sexp (dump_directory ^/ "state.sexp") ([%sexp_of: t] t)
    in
    let%bind (description, next) = step t in
    let%bind () =
      Writer.save_sexp (dump_directory ^/ "step.sexp")
        ([%sexp_of: Step.t] description)
    in
    let%bind () =
      Writer.save_sexp (dump_directory ^/ "initial_tree.sexp")
        ([%sexp_of: Inlining_tree.Top_level.t] (fst description.initial).tree)
    in
    let%bind () =
      Writer.save_sexp (dump_directory ^/ "proposal_tree.sexp")
        ([%sexp_of: Inlining_tree.Top_level.t] (fst description.proposal).tree)
    in
    return (description, next)
  ;;
end

let command =
  let open Command.Let_syntax in
  Command.async_or_error' ~summary:"Command"
    [%map_open
      let {
        Command_params.
        config_filename;
        controller_rundir;
        exp_dir;
        bin_name;
        bin_args;
       } = Command_params.params
      in
      fun () ->
        Reader.load_sexp config_filename [%of_sexp: Config.t]
        >>=? fun config ->
        Deferred.Or_error.List.map config.worker_configs ~how:`Parallel
          ~f:(fun worker_config ->
            let hostname = Protocol.Config.hostname worker_config in
            Experiment_utils.init_connection ~hostname ~worker_config)
        >>=? fun worker_connections ->
        Log.Global.sexp ~level:`Info [%message "building initial state" ];
        Utils.get_initial_state ~bin_name ~exp_dir ~base_overrides:[] ()
        >>=? fun initial_state ->
        let initial_state = Option.value_exn initial_state in
        let process conn work_unit =
          let path_to_bin = work_unit.Work_unit.path_to_bin in
          let num_runs =
            config.num_runs / List.length config.worker_configs
          in
          Experiment_utils.run_binary_on_worker
            ~num_runs ~config ~conn ~path_to_bin
            ~hostname:(Utils.Worker_connection.hostname conn)
            ~bin_args
          >>|? fun execution_stats ->
          let raw_execution_time = execution_stats.raw_execution_time in
          Log.Global.sexp ~level:`Info [%message
            (path_to_bin : string)
            (raw_execution_time : Time.Span.t list)];
          Log.Global.info "%s\n" execution_stats.gc_stats;
          execution_stats
        in
        lift_deferred (Utils.Scheduler.create worker_connections ~process)
        >>=? fun scheduler ->
        (* We run this 3 more times than the others to gurantee
         * stability of the distribution of initial execution times.
         *)
        Deferred.Or_error.List.init ~how:`Sequential 3 ~f:(fun i ->
          Log.Global.sexp ~level:`Info
            [%message "Initial state run " (i : int)];
          Deferred.Or_error.List.init (List.length config.worker_configs)
            ~how:`Parallel
            ~f:(fun _ ->
              let path_to_bin = initial_state.path_to_bin in
              let work_unit =
                { Work_unit. path_to_bin; step = -1; sub_id = 0; }
              in
              Utils.Scheduler.dispatch scheduler work_unit))
        >>=? fun stats ->
        let stats = List.concat_no_order stats in
        let initial_execution_times =
          List.concat_map stats ~f:(fun stat -> stat.raw_execution_time)
        in
        let initial_execution_stats =
          { Execution_stats.
            raw_execution_time = initial_execution_times;
            worker_hostname = None;
            gc_stats = (List.hd_exn stats).gc_stats;
            parsed_gc_stats = None;
          }
        in
        let initial_execution_time =
          List.map ~f:Time.Span.to_sec initial_execution_times
          |> geometric_mean
          |> Time.Span.of_sec
        in
        printf !"Initial Execution Time = %{Time.Span}\n"
          initial_execution_time;
        let module Annealer = Make_annealer(struct
          let bin_name = bin_name
          let exp_dir = exp_dir
          let initial_execution_time = initial_execution_time
          let scheduler = scheduler
          let run_dir = controller_rundir
        end)
        in
        let state =
          let tree = initial_state.traversal_state.tree_root in
          let path_to_bin = initial_state.path_to_bin in
          let work_unit =
            { Work_unit. path_to_bin; step = 0; sub_id = -1; }
          in
          let initial = { Annealer.T. tree; work_unit; } in
          Annealer.empty initial initial_execution_stats
        in
        Deferred.repeat_until_finished state (fun state ->
          Annealer.step state
          >>| fun ((_ : Annealer.Step.t), next) ->
          if next.step >= next.config.steps then
            `Finished (Ok ())
          else
            `Repeat next
        )
    ]
