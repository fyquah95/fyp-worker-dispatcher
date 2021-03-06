[@@@ocaml.warning "+a-4-9-30-40-41-42-44"]

open Core
open Async
open Common


let with_file_lock lock_name ~f =
  let lockdir = "/home/fyquah/.fyp-locks/" in
  let filename = lockdir ^/ lock_name in
  let%bind fd =
    Deferred.repeat_until_finished () (fun () ->
      Monitor.try_with (fun () ->
          let mode = [ `Creat; `Excl; `Rdwr; ] in
          Unix.openfile filename ~mode)
      >>= function
      | Ok fd -> Deferred.return (`Finished fd)
      | Error exn ->
        Log.Global.info
          "Failed to acquite lock for %s. Waiting for a few seconds."
          lock_name;
        Log.Global.sexp [%message (exn: exn)];
        Clock.after (Time.Span.of_sec 3.1415926535)
        >>= fun () -> Deferred.return (`Repeat ()))
  in
  let clean = ref false in
  let clean_up () =
    if not !clean then begin
      clean := true;
      let%bind () = Unix.close fd in
      Unix.unlink filename
    end else
      Deferred.unit
  in
  Monitor.protect
    (fun () ->
       f () >>= fun r ->
       clean_up () >>= fun () ->
       Deferred.return r)
    ~finally:clean_up
;;

module Work_unit = struct
  type deprecated =
    { path_to_bin : string;
      step : int;
      sub_id: int;
    }
  [@@deriving of_sexp]

  type t =
    { path_to_bin : string;
      step : [ `Step of int | `Initial ];
      sub_id : [ `Sub_id of int | `Current ];
    }
  [@@deriving sexp]

  let t_of_sexp sexp =
    try
      t_of_sexp sexp
    with
    | _ ->
      let deprecated = deprecated_of_sexp sexp in
      { path_to_bin = deprecated.path_to_bin;
        step = `Step deprecated.step;
        sub_id = `Sub_id deprecated.sub_id;
      }

end

module Dump_utils = struct

  let (rundir : string option ref) = ref None

  let set_controller_rundir s = rundir := Some s

  let execution_dump_directory ~step ~sub_id =
    let step =
      match step with
      | `Initial -> "initial"
      | `Step x -> string_of_int x
    in
    let sub_id =
      match sub_id with
      | `Current -> "current"
      | `Sub_id x -> string_of_int x
    in
    let rundir = Option.value_exn !rundir in
    rundir ^/ "opt_data" ^/ step ^/ sub_id
  ;;
end


module Initial_state = struct
  type t =
    { v0_decisions : Data_collector.V0.t list;
      v1_decisions : Data_collector.V1.Decision.t list;
      path_to_bin  : string;
    }
end

let read_decisions ~round ~exp_dir ~module_paths =
  let open Deferred.Or_error.Let_syntax in
  assert (0 <= round && round < 3);
  let%bind v0_decisions =
    List.map module_paths ~f:(fun filename ->
      exp_dir ^/ (filename ^ sprintf ".%d.data_collector.v0.sexp" round))
    |> List.rev
    |> Deferred.Or_error.List.concat_map ~f:(fun filename ->
        Log.Global.info "Reading v0 decisions from %s" filename;
        Reader.load_sexp filename [%of_sexp: Data_collector.V0.t list])
  in
  let%bind v1_decisions =
    List.map module_paths ~f:(fun filename ->
      exp_dir ^/ (filename ^ sprintf ".%d.data_collector.v1.sexp" round))
    |> Deferred.Or_error.List.concat_map ~f:(fun filename ->
        Log.Global.info "Reading v1 decisions from %s" filename;
        Reader.load_sexp filename
          [%of_sexp: Data_collector.V1.Decision.t list])
  in
  return (v0_decisions, v1_decisions)
;;

let get_initial_state ?(env = []) ~round ~module_paths ~bin_name ~exp_dir ~base_overrides () =
  let open Deferred.Or_error.Let_syntax in
  Log.Global.info "Compiling first version of source tree.";
  let%bind () =
    shell ~dir:exp_dir "make" [ "clean" ] >>=? fun () ->
    lift_deferred (
      Writer.save_sexp (exp_dir ^/ "overrides.sexp")
        ([%sexp_of: Data_collector.V0.t list] base_overrides)
    )
    >>=? fun () ->
    shell ~env:env ~dir:exp_dir "make" [ "all" ]
  in
  let%bind (v0_decisions, v1_decisions) =
    read_decisions ~round ~exp_dir ~module_paths
  in
  let path_to_bin =
    Filename.temp_file "fyp-" ("-" ^ bin_name)
  in
  let%map () =
    shell ~dir:exp_dir
      "cp" [ (bin_name ^ ".native"); path_to_bin ]
    >>= fun () ->
    shell ~dir:exp_dir "chmod" [ "755"; path_to_bin ]
  in
  let v0_decisions = filter_v0_decisions v0_decisions in
  Some {
    Initial_state.
    v0_decisions;
    v1_decisions;
    path_to_bin;
  }
;;

module Worker_connection = struct
  type 'a rpc_conn =
    { hostname : string;
      socket : ([`Active], 'a) Socket.t;
      reader : Reader.t;
      writer : Writer.t;
      rpc_connection : Rpc.Connection.t
    }

  type ssh_conn =
    { user: string;
      hostname: string;
      rundir: string;
      processor: int option;
    }

  type 'a t =
    | Rpc : 'a rpc_conn -> 'a t
    | Ssh : ssh_conn -> 'a t

  let hostname (type a) (t : a t) =
    match t with
    | Rpc c -> c.hostname
    | Ssh c -> c.hostname

  let processor (type a) (t : a t) =
    match t with
    | Rpc _ -> None
    | Ssh c -> c.processor
  ;;

  let lockname a = hostname a
end

let run_binary_on_rpc_worker
    ~hostname
    ~(worker_connection : 'a Worker_connection.rpc_conn)
    ~path_to_bin =
  let conn = worker_connection.rpc_connection in
  Rpc.Rpc.dispatch Info_rpc.rpc conn Info_rpc.Query.Where_to_copy
  >>=? fun path_to_rundir ->
  (* TODO: remove hard code *)
  let abs_path_to_rundir =
    path_to_rundir.Info_rpc.Response.rundir
    ^/ Relpath.to_string (path_to_rundir.Info_rpc.Response.relpath)
  in
  let args = [ path_to_bin; sprintf "%s:%s" hostname abs_path_to_rundir ] in
  shell ~dir:(Filename.dirname path_to_bin) "scp" args
  >>=? fun () ->
  let query =
    Job_dispatch_rpc.Query.Run_binary (
      Relpath.of_string (
        Relpath.to_string path_to_rundir.relpath
        ^/ Filename.basename path_to_bin
      )
    )
  in
  Rpc.Rpc.dispatch Job_dispatch_rpc.rpc conn query
  >>=? fun response ->
  match response with
  | Success results -> Deferred.Or_error.return results
  | Failed e -> Deferred.return (Error e)
;;

let run_binary_on_ssh_worker ~num_runs ~processor ~rundir ~user ~hostname
    ~path_to_bin ~bin_args ~dump_dir ~bin_files =
  lift_deferred (Unix.getcwd ())
  >>=? fun dir ->
  let copy_files_over () =
    match bin_files with
    | [] -> Deferred.Or_error.ok_unit
    | bin_files ->
      shell ~dir "scp"
        (bin_files @ [ sprintf "%s@%s:%s" user hostname (rundir ^/ "files") ])
  in
  shell ~dir "scp"
    [ path_to_bin;
      sprintf "%s@%s:%s" user hostname (rundir ^/ "binary.exe");
    ]
  >>=? fun () ->
  shell ~dir "ssh"
    [ sprintf "%s@%s" user hostname;
      rundir ^/ "clean_up.sh";
    ]
  >>=? fun () ->
  copy_files_over ()
  >>=? fun () ->
  let processor = Option.value ~default:0 processor in
  let taskset_mask = sprintf "0x%x" (1 lsl processor) in
  let args = [
    sprintf "%s@%s" user hostname;
    rundir ^/ "benchmark_binary.sh";
    rundir ^/ "binary.exe";
    Int.to_string num_runs;
    taskset_mask;
    bin_args;
  ]
  in
  let benchmark_args = String.concat ~sep:" " args in
  Log.Global.sexp ~level:`Info [%message (benchmark_args: string)];
  Async_shell.run_lines ~working_dir:dir "ssh" args
  >>= function
  | [] -> Deferred.Or_error.error_s [%message "Something went wrong"]
  | lines ->
    let raw_execution_time =
      List.map lines ~f:(fun line ->
          Time.Span.of_sec (Float.of_string line))
    in
    let worker_hostname = Some hostname in
    Async_shell.run_full ~working_dir:dir "ssh" [
      sprintf "%s@%s" user hostname;
      rundir ^/ "perf.sh";
      rundir ^/ "binary.exe";
      taskset_mask;
      bin_args;
    ]
    >>= fun perf_output ->
    shell ~dir "scp" [
      sprintf "%s@%s:%s" user hostname (rundir ^/ "perf.data");
      dump_dir ^/ "perf.data";
    ]
    >>=? fun () ->
    (* Compress [perf.data] in the dump directory, otherwise file size
     * problems will occur
     *)
    shell ~dir "tar" [
      "zcf";
      (dump_dir ^/ "perf.data.tar");
      "-C";
      dump_dir;
      "perf.data";
    ]
    >>=? fun () -> shell ~dir "rm" [ dump_dir ^/ "perf.data" ]
    (* We don't, copy the generated binary, as that's really compilation
     * artifact rather than run-time information. But if we really want to,
     * it's the following line of code.

     >>=? fun () -> shell ~dir "cp" [ path_to_bin; dump_dir ]

     *)
    >>=? fun () ->
    match String.split ~on:'*' perf_output with
    | [perf_one; perf_two; gc_stats] ->
      let perf_one = Execution_stats.Perf_stats.parse perf_one in
      let perf_two = Execution_stats.Perf_stats.parse perf_two in
      let parsed_gc_stats =
        Execution_stats.Gc_stats.parse (String.split_lines gc_stats)
      in
      let execution_stats =
        { Execution_stats.
          raw_execution_time;
          worker_hostname;
          gc_stats;
          parsed_gc_stats;
          perf_stats = Some [ perf_one; perf_two ];
        }
      in
      lift_deferred (
        Writer.save_sexp (dump_dir ^/ "execution_stats.sexp")
          ([%sexp_of: Execution_stats.t] execution_stats)
      )
      >>|? fun () -> execution_stats

    | _ -> Deferred.Or_error.error_string "Cannot parse perf output!"
;;

let run_binary_on_worker (type a)
    ~num_runs ~processor ~hostname
    ~(conn: a Worker_connection.t) ~path_to_bin ~bin_args ~dump_dir ~bin_files =
  with_file_lock (Worker_connection.lockname conn) ~f:(fun () ->
    match conn with
    | Worker_connection.Rpc worker_connection ->
      run_binary_on_rpc_worker ~worker_connection ~path_to_bin ~hostname
    | Worker_connection.Ssh (ssh_config : Worker_connection.ssh_conn) ->
      let rundir = ssh_config.rundir in
      let user = ssh_config.user in
      run_binary_on_ssh_worker ~processor ~num_runs ~user ~rundir ~hostname
        ~path_to_bin ~bin_args ~dump_dir ~bin_files)
;;

let init_connection ~hostname ~worker_config =
  match worker_config with
  | Protocol.Config.Ssh_worker ssh_config ->
    begin
    lift_deferred (Unix.getcwd ())
    >>=? fun cwd ->
    with_file_lock hostname ~f:(fun () ->
        let user = ssh_config.user in
        let args =
          [ "worker/clean_up.sh";
            (sprintf "%s@%s:%s" user hostname ssh_config.rundir);
          ]
        in
        shell ~dir:cwd "scp" args >>=? fun () ->
        let args =
          [ "worker/benchmark_binary.sh";
            (sprintf "%s@%s:%s" user hostname ssh_config.rundir);
          ]
        in
        shell ~dir:cwd "scp" args >>=? fun () ->
        let args =
          [ "worker/perf.sh";
            (sprintf "%s@%s:%s" user hostname ssh_config.rundir);
          ]
        in
        shell ~dir:cwd "scp" args >>|? fun () ->
        let rundir = ssh_config.rundir in
        let user = ssh_config.user in
        let processor = ssh_config.processor in
        Worker_connection.Ssh { user; rundir; hostname; processor; })
    end
  | Protocol.Config.Rpc_worker rpc_config ->
      
    lift_deferred (
      Tcp.connect
        (Tcp.Where_to_connect.of_host_and_port
          (Host_and_port.create ~host:hostname ~port:rpc_config.port))
        ~timeout:(Time.Span.of_int_sec 1)
    )
    >>=? fun (socket, reader, writer) ->
    Rpc.Connection.create reader writer ~connection_state:(fun _ -> ())
    >>| function
    | Ok rpc_connection ->
      let rpc_connection =
        { Worker_connection.
          hostname; socket; reader; writer; rpc_connection; }
      in
      Ok (Worker_connection.Rpc rpc_connection)
    | Error exn -> raise exn


module Scheduler = struct
  type 'a availble_workers =
    | Available of 'a Worker_connection.t * 'a Worker_connection.t list
    | Completed

  type ('a, 'b, 'c) t =
    { queue       :
        ('c availble_workers,
         [ `Read | `Who_can_write of Core_kernel__Perms.me ]) Mvar.t;
      process     : ('c Worker_connection.t -> 'a -> 'b Deferred.t);
      num_workers : int;
    }

  let num_workers t = t.num_workers

  let add_conn_to_queue q conn =
    match q with
    | None -> Available (conn, [])
    | Some (Available (hd, tl)) -> Available (conn, hd :: tl)
    | Some Completed -> Completed
  ;;

  let create connections ~process =
    let worker_queue = Mvar.create () in
    Deferred.List.iter connections
      ~how:`Parallel ~f:(fun conn ->
        Mvar.update worker_queue ~f:(fun q -> add_conn_to_queue q conn) ;
        Deferred.unit)
    >>| fun () ->
    { queue = worker_queue; process; num_workers = List.length connections; }
  ;;

  let dispatch t a =
    let%bind avail = Mvar.take t.queue in
    begin match avail with
    | Completed -> failwith "Dispatching working to completed scheduler"
    | Available (conn, left) ->
      match left with
      | [] -> Deferred.return conn
      | hd :: tl -> Mvar.put t.queue (Available (hd, tl)) >>| fun () -> conn
    end
    >>= fun conn -> t.process conn a
    >>| fun result ->
    Mvar.update t.queue ~f:(fun q -> add_conn_to_queue q conn);
    result
  ;;

  let terminate t = Mvar.set t.queue Completed
end

let process_work_unit
    ~(num_runs : int) ~(bin_args: string) ~(bin_files)
    (conn: 'a Worker_connection.t) (work_unit: Work_unit.t) =
  let path_to_bin = work_unit.Work_unit.path_to_bin in
  let processor = Worker_connection.processor conn in
  let dump_dir =
    Dump_utils.execution_dump_directory ~step:work_unit.step
      ~sub_id:work_unit.sub_id
  in
  lift_deferred (Async_shell.mkdir ~p:() dump_dir)
  >>=? fun () ->
  run_binary_on_worker ~processor
    ~num_runs ~conn ~path_to_bin
    ~hostname:(Worker_connection.hostname conn)
    ~bin_args ~dump_dir ~bin_files
  >>=? fun execution_stats ->
  let raw_execution_time = execution_stats.raw_execution_time in
  Log.Global.sexp ~level:`Info [%message
    (path_to_bin : string)
    (raw_execution_time : Time.Span.t list)];
  let stats =
    Option.value_exn (
      Execution_stats.Gc_stats.parse
        (String.split_lines execution_stats.gc_stats)
    )
  in
  let prefix = "[" ^ path_to_bin ^ " GC STATS]" in
  Log.Global.info "%s major collections: %d" prefix stats.major_collections;
  Log.Global.info "%s minor collections: %d" prefix stats.minor_collections;
  Log.Global.info "%s compactions: %d" prefix stats.compactions;
  lift_deferred (
    Writer.save_sexp (dump_dir ^/ "execution_stats.sexp")
      ([%sexp_of: Protocol.Execution_stats.t] execution_stats)
  )
  >>|? fun () ->
  execution_stats
;;

let copy_compilation_artifacts ~exp_dir ~dump_dir ~abs_path_to_binary =
  let copy_with_wildcard src dest =
    shell ~dir:exp_dir "bash" [ "-c"; sprintf "cp -f %s %s" src dest ]
  in
  let artifacts_directory = dump_dir ^/ "artifacts" in
  lift_deferred (Async_shell.mkdir ~p:() artifacts_directory)
  >>=? fun () -> copy_with_wildcard (exp_dir ^/ "overrides.sexp") artifacts_directory
  >>=? fun () -> copy_with_wildcard (exp_dir ^/ "*.sexp") artifacts_directory
  >>=? fun () -> copy_with_wildcard (exp_dir ^/ "*.s") artifacts_directory
  >>=? fun () -> copy_with_wildcard (exp_dir ^/ "stdout.log") artifacts_directory
  >>=? fun () -> copy_with_wildcard (exp_dir ^/ "flambda.out") artifacts_directory
  >>=? fun () -> copy_with_wildcard (exp_dir ^/ "*.native") artifacts_directory
  >>=? fun () -> copy_with_wildcard abs_path_to_binary artifacts_directory
  >>=? fun () ->
  shell ~dir:exp_dir "tar" [
    "zcf";
    (dump_dir ^/ "artifacts.tar");
    "-C";
    artifacts_directory;
    ".";
  ]
  >>=? fun () ->
  shell ~dir:exp_dir "rm" [ "-rf"; artifacts_directory; ]
;;

let compile_binary ~dir ~bin_name ~write_overrides ~dump_directory =
  let filename = Filename.temp_file "fyp-" ("-" ^ bin_name) in
  lift_deferred (Async_shell.mkdir ~p:() dump_directory)
  >>=? fun () ->
  lift_deferred (Async_shell.run_full "which" ["ocamlopt.opt"])
  >>=? fun where_is_ocamlopt ->
  let ocamlparam =
    Option.value ~default:"_" (Sys.getenv "OCAMLPARAM")
  in
  let timeout =
    Option.value ~default:"30m" (Sys.getenv "FYP_COMPILATION_TIMEOUT")
  in
  (Log.Global.info "ocamlopt path = %s" where_is_ocamlopt);
  (Log.Global.info "OCAMLPARAM = %s" ocamlparam);
  Deferred.Or_error.ok_unit
  >>=? fun () -> shell ~dir "make" [ "clean" ]
  >>=? fun () -> write_overrides (dir ^/ "overrides.sexp")
  >>=? fun () -> shell ~dir "timeout" [ timeout; "make"; "all" ]
  >>=? fun () -> shell ~dir "cp" [ (bin_name ^ ".native"); filename ]
  >>=? fun () -> shell ~dir "chmod" [ "755"; filename ]
  >>=? fun () ->
  copy_compilation_artifacts ~exp_dir:dir ~dump_dir:dump_directory
    ~abs_path_to_binary:filename
  >>=? fun () -> Deferred.Or_error.return filename
;;

(** Useful to get a stable estimate of the baseline runtime **)
let run_in_all_workers ~scheduler ~times ~config ~path_to_bin =
  (* We run this 3 more times than the others to gurantee
   * stability of the distribution of initial execution times.
   *)
  Deferred.Or_error.List.init ~how:`Sequential times ~f:(fun i ->
    Log.Global.sexp ~level:`Info
      [%message "Initial state run " (i : int)];
    Deferred.Or_error.List.init (List.length config.Config.worker_configs)
      ~how:`Parallel
      ~f:(fun j ->
        let sub_id = `Sub_id (j + i * List.length config.worker_configs) in
        let work_unit =
          { Work_unit. path_to_bin; step = `Initial; sub_id; }
        in
        Scheduler.dispatch scheduler work_unit))
  >>=? fun stats ->
  let stats = List.concat_no_order stats in
  let initial_execution_times =
    List.concat_map stats ~f:(fun (stat : Execution_stats.t) -> stat.raw_execution_time)
  in
  let perf_stats =
    List.concat_map stats ~f:(fun s ->
        Option.value ~default:[] s.Execution_stats.perf_stats)
  in
  let perf_stats = match perf_stats with | [] -> None | _ -> Some perf_stats in
  let execution_stats =
    { Execution_stats.
      raw_execution_time = initial_execution_times;
      worker_hostname = None;
      gc_stats = (List.hd_exn stats).gc_stats;
      parsed_gc_stats = None;
      perf_stats;
    }
  in
  Deferred.Or_error.return execution_stats
;;

