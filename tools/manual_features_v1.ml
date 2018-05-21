open Core
open Async
open Fyp_compiler_lib
open Protocol.Shadow_fyp_compiler_lib
       
open Common

let process (query : Inlining_query.query) =
  let calc_t_matces expr ~f =
    let ctr = ref 0 in
    Flambda.iter_general ~toplevel:true
      (fun (t : Flambda.t) ->
         if f t then
           ctr := !ctr + 1)
      (fun (_named : Flambda.named) -> ())
      (Flambda.Is_expr expr);
    !ctr
  in
  let calc_named_matches expr ~f =
    let ctr = ref 0 in
    Flambda.iter_general ~toplevel:true
      (fun (_t : Flambda.t) -> ())
      (fun (named : Flambda.named) ->
         if f named then
           ctr := !ctr + 1)
      (Flambda.Is_expr expr);
    !ctr
  in
  let env = query.env in
  let calc_matching_approximations_from_env ~prefix (expr : Flambda.t) =
    let num_symbol_approximations =
      calc_named_matches expr ~f:(function
          | Flambda.Symbol s -> Symbol.Map.mem s env.approx_sym
          | _ -> false)
      |> Float.of_int
    in
    let num_mutable_approximations =
      calc_named_matches expr ~f:(function
          | Flambda.Read_mutable mvar ->
            Mutable_variable.Map.mem mvar env.approx_mutable
          | _ -> false)
      |> Float.of_int
    in
    let num_var_approximations =
      calc_t_matces expr ~f:(function
          | Flambda.Var var -> Variable.Map.mem var env.approx
          | _ -> false)
      |> Float.of_int
    in
    let num_closure_movements_matches =
      calc_named_matches expr ~f:(fun expr ->
          match expr with
          | Flambda.Move_within_set_of_closures msvc ->
            Projection.Map.mem
              (Projection.Move_within_set_of_closures msvc)
              env.projections
          | _ -> false)
      |> Float.of_int
    in
    let num_project_closure_matches =
      calc_named_matches expr ~f:(fun expr ->
          match expr with
          | Flambda.Project_closure pc ->
            Projection.Map.mem
              (Projection.Project_closure pc)
              env.projections
          | _ -> false)
      |> Float.of_int
    in
    let num_project_var_matches =
      calc_named_matches expr ~f:(fun expr ->
          match expr with
          | Flambda.Project_var pv ->
            Projection.Map.mem
              (Projection.Project_var pv)
              env.projections
          | _ -> false)
      |> Float.of_int
    in
    let p = prefix in
    let numeric_features =
      Feature_list.of_list [
        (p ^ "_symbol_approx",              num_symbol_approximations);
        (p ^ "_mutable_approx",             num_mutable_approximations);
        (p ^ "_var_approx",                 num_var_approximations);
        (p ^ "_move_within_closure_approx", num_closure_movements_matches);
        (p ^ "_project_closure_approx",     num_project_closure_matches);
        (p ^ "_project_var_approx",         num_project_var_matches);
      ]
    in
    let int_features = Feature_list.empty in
    let bool_features = Feature_list.empty in
    { Features. numeric_features; int_features; bool_features }
  in
  let callee_features ~prefix expr =
    let size = Inlining_cost.lambda_size expr |> Float.of_int in
    let num_const_int =
      calc_named_matches expr ~f:(fun (named : Flambda.named) ->
          match named with
          | Flambda.Const (Flambda.Char _)
          | Flambda.Const (Flambda.Int _) -> true
          | _ -> false)
      |> Float.of_int
    in
    let num_const_pointer =
      calc_named_matches expr ~f:(fun (named : Flambda.named) ->
          match named with
          | Flambda.Const (Flambda.Const_pointer _) -> true
          | _ -> false)
      |> Float.of_int
    in
    let p = prefix in
    let numeric_features =
      Feature_list.of_list [
        (p ^ "_const_int", num_const_int);
        (p ^ "_const_pointer", num_const_pointer);
        (p ^ "_size", size);
      ]
    in
    let int_features = Feature_list.empty in
    let bool_features = Feature_list.empty in
    { Features. numeric_features; int_features; bool_features; }
  in
  (* Callee features that definitely does not change after inlining *)
  let persistent_callee_features =
    let numeric_features = Feature_list.empty in
    let int_features  = Feature_list.empty in
    let direct_call =
      match query.call_kind with
      | Flambda.Indirect -> false
      | Flambda.Direct _ -> true
    in
    let bool_features =
      Feature_list.of_list [
        ("is_a_functor",         query.function_decl.is_a_functor);
        ("only_use_of_function", query.only_use_of_function);
        ("direct_call",          direct_call);
      ]
    in
    { Features. numeric_features; int_features; bool_features; }
  in
  (* Features about the arguments being passed into the function. *)
  let args_features =
    let classify_arg arg =
      match arg with
      | None -> 0
      | Some descr ->
        match (descr : Simple_value_approx.descr) with
        | Value_block _ -> 1
        | Value_int _ -> 1
        | Value_char _ -> 2
        | Value_constptr _ -> 3
        | Value_float _ -> 4
        | Simple_value_approx.Value_boxed_int _ -> 5
        | Value_set_of_closures _ -> 6
        | Value_closure _ -> 7
        | Value_string _ -> 8
        | Value_float_array _ -> 9
        | Value_unknown _ -> 10
        | Value_bottom -> 11
        | Value_extern _ -> 12
        | Value_symbol _ -> 13
        | Value_unresolved _ -> 14
    in
    let int_features =
      List.init 7 ~f:(fun i ->
          let opt_descr =
            let open Option.Let_syntax in
            let%bind var = List.nth query.args i in
            let%map (scope, sva) = Variable.Map.find_opt var query.env.approx in
            sva.descr
          in
          opt_descr
          |> classify_arg
          |> (fun feature -> (sprintf "approx_arg_%d" i, feature)))
      |> Feature_list.of_list
    in
    let bool_features = Feature_list.empty in
    let numeric_features = Feature_list.empty in
    { Features. int_features; bool_features; numeric_features; }
  in
  (* Features about the parameters, w/o regards to what is being passed in.
   * This can be a consequence due to modifications caused by specialisaion
   * and invariant params.
   *)
  let parameter_features =
    let num_invariant_params =
      let var = Closure_id.unwrap query.closure_id_being_applied in
      Variable.Map.find_opt var query.value_set_of_closures.invariant_params
      |> Option.map ~f:Variable.Set.cardinal
      |> Option.value ~default:0
      |> Float.of_int
    in
    let num_specialised_args =  (* TODO: Is this even valid? *)
      let params = query.function_decl.params in
      List.filter params ~f:(fun p ->
          let var = Parameter.var p in
          Variable.Map.mem var query.value_set_of_closures.specialised_args)
      |> List.length
      |> Float.of_int
    in
    let num_params = List.length query.function_decl.params |> Float.of_int in
    let numeric_features =
      Feature_list.of_list [
        ("num_invariant_params", num_invariant_params);
        ("num_specialised_args", num_specialised_args);
        ("num_params", num_params);
      ]
    in
    let int_features = Feature_list.empty in
    let bool_features = Feature_list.empty in
    { Features. numeric_features; int_features; bool_features; } 
  in
  let env_features =
    let int_features =
      Feature_list.of_list [
        ("inlining_level", env.inlining_level);
        ("closure_depth",  env.closure_depth);
        ("inside_branch",  env.inside_branch);
      ]
    in
    let numeric_features = Feature_list.empty in
    let bool_features    = Feature_list.empty in
    { Features. numeric_features; int_features; bool_features; }
  in
  let open Features in
  calc_matching_approximations_from_env ~prefix:"original" query.original
  @ calc_matching_approximations_from_env ~prefix:"inlined" query.inlined_result.body
  @ env_features
  @ parameter_features
  @ callee_features ~prefix:"original" query.original
  @ callee_features ~prefix:"inlined" query.inlined_result.body
  @ persistent_callee_features
  @ args_features
;;


let command =
  let open Command.Let_syntax in
  Command.async ~summary:"Manual hand features v1"
    [%map_open
      let filelist =
        flag "-filelist" (required string) ~doc:"FILE data source"
      and output =
        flag "-output" (required string) ~doc:"FILE output"
      in
      fun () ->
        let open Deferred.Let_syntax in
        let%bind filelist = Reader.file_lines filelist in
        let%bind all_features =
          Io_helper.load_queries ~allow_repeat:`Only_across_files ~filelist
          |> Pipe.map ~f:process
          |> Pipe.to_list
        in
        let names = Features.names (List.hd_exn all_features) in
        let header = String.concat ~sep:"," names in
        let rows =
          List.map all_features ~f:(fun features ->
              List.map names ~f:(fun name ->
                match Features.find_exn features name with
                  | `Numeric x -> x
                  | `Int x -> Float.of_int x
                  | `Bool x -> (if x then 1.0 else 0.0))
              |> List.map ~f:Float.to_string
              |> String.concat ~sep:",")
        in
        let%bind () =
          Writer.with_file output ~f:(fun wrt ->
              Writer.write_line wrt header;
              List.iter rows ~f:(Writer.write_line wrt);
              Deferred.unit)
        in
        return ()
    ]
;;