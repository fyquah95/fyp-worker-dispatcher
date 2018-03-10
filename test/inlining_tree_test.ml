open Core
open Async

module Inlining_tree = Protocol.Inlining_tree
module Data_collector = Protocol.Shadow_fyp_compiler_lib.Data_collector

module Test_v0 = struct
  module Data_collector = Data_collector.V0
  module Inlining_tree = Inlining_tree.V0

  let name = "compiler-call-site-offset"

  let datadir = Testlib.repo_root ^/ "test/testdata/call-site-offset"

  let load_decision_tree () =
    let%bind decisions =
      Reader.load_sexp_exn (datadir ^/ "main.0.data_collector.v0.sexp")
        [%of_sexp: Data_collector.t list]
    in
    let decision_tree = Inlining_tree.build decisions in
    return decision_tree
  ;;

  let compile_with_decisions root =
    let working_dir = datadir in
    let decisions = Inlining_tree.Top_level.to_override_rules root in
    let%bind () =
      Writer.save_sexp (datadir ^/ "overrides.sexp")
        ([%sexp_of: Data_collector.t list] decisions)
    in
    Async_shell.run ~echo:true ~verbose:true ~expect:[ 0 ] ~working_dir
      "bash" [ "-c"; "make all" ]
  ;;

  let run () =
    let%bind () = compile_with_decisions [] in
    let%bind tree = load_decision_tree () in
    printf "tree 1:\n%s" (Format.asprintf "%a" Inlining_tree.Top_level.pp tree);
    let modified_tree =
      let leaves = Inlining_tree.Top_level.count_leaves tree in
      Inlining_tree.Top_level.flip_nth_leaf tree (leaves - 1)
    in
    printf "flipped tree:\n%s" (Format.asprintf "%a" Inlining_tree.Top_level.pp modified_tree);
    let%bind () = compile_with_decisions modified_tree in
    let%bind tree = load_decision_tree () in
    printf "tree 2:\n%s" (Format.asprintf "%a" Inlining_tree.Top_level.pp tree);
    Deferred.unit
end

module Test_v1 = struct
  module Data_collector = Data_collector.V1
  module Inlining_tree = Inlining_tree.V1

  let datadir = Testlib.repo_root ^/ "test/testdata/call-site-offset"

  let load_decision_tree () =
    let%bind decisions =
      Reader.load_sexp_exn (datadir ^/ "main.0.data_collector.v1.sexp")
        [%of_sexp: Data_collector.Decision.t list]
    in
    let decision_tree = Inlining_tree.build decisions in
    return decision_tree
  ;;

  let run_single_iter ~iter ~tree ~compile_with_decisions =
    let modified_tree =
      let rec make () =
        if Random.bool () then
          let leaves = Inlining_tree.Top_level.count_leaves tree in
          Inlining_tree.Top_level.flip_nth_leaf tree (Random.int leaves)
        else
          let leaves = Inlining_tree.Top_level.count_leaves tree in
          match
            Inlining_tree.Top_level.backtrack_nth_leaf tree (Random.int leaves)
          with
          | None -> make ()
          | Some x -> x
      in
      make ()
    in
    let%bind () = compile_with_decisions modified_tree in
    let%map compiled_tree = load_decision_tree () in

    assert (not (Inlining_tree.Top_level.compare tree modified_tree = 0));
    let buffer = Buffer.create 1000 in
    Inlining_tree.Top_level.pprint buffer compiled_tree;
    printf "COMPILED:\n%s\n" (Buffer.contents buffer);

    let buffer = Buffer.create 1000 in
    Inlining_tree.Top_level.pprint buffer modified_tree;
    printf "TARGET:\n%s\n" (Buffer.contents buffer);

    assert (Inlining_tree.Top_level.is_super_tree
      ~super:compiled_tree modified_tree);

    compiled_tree
  ;;

  let compile_with_decisions ~overrides_sexp_of_tree root =
    let working_dir = datadir in
    let%bind () =
      Async_shell.run ~echo:true ~verbose:true ~expect:[ 0 ] ~working_dir
        "bash" [ "-c"; "make clean" ]
    in
    let%bind () =
      let overrides = overrides_sexp_of_tree root in
      Writer.save_sexp (datadir ^/ "overrides.sexp") overrides
    in
    Async_shell.run ~echo:true ~verbose:true ~expect:[ 0 ] ~working_dir
      "bash" [ "-c"; "make all" ]
  ;;

  module Verify_v1_overrides = struct
    let name = "v1.verify_v1_overrides"

    let overrides_sexp_of_tree root =
      [%sexp_of: Data_collector.Overrides.t] (
        Inlining_tree.Top_level.to_override_rules root
      )
    ;;

    let compile_with_decisions =
      compile_with_decisions ~overrides_sexp_of_tree
    ;;

    let run () =
      printf "Running initial complation\n";
      let%bind () = compile_with_decisions [] in
      let%bind tree = load_decision_tree () in
      Deferred.ignore (
        List.init 10 ~f:Fn.id
        |> Deferred.List.fold ~init:tree ~f:(fun tree iter  ->
            run_single_iter ~iter ~tree ~compile_with_decisions)
      )
  end

  module Verify_simple_overrides = struct
    let name = "v1.verify_simple_overrides"

    let overrides_sexp_of_tree root =
      [%sexp_of: Data_collector.Simple_overrides.t] (
        Inlining_tree.Top_level.to_simple_overrides root
      )
    ;;

    let compile_with_decisions =
      compile_with_decisions ~overrides_sexp_of_tree
    ;;

    let run () =
      printf "Running initial complation\n";
      let%bind () = compile_with_decisions [] in
      let%bind tree = load_decision_tree () in
      Deferred.ignore (
        List.init 10 ~f:Fn.id
        |> Deferred.List.fold ~init:tree ~f:(fun tree iter  ->
            run_single_iter ~iter ~tree ~compile_with_decisions)
      )
  end
end

let () =
  Testlib.register (module Test_v1.Verify_v1_overrides);
  Testlib.register (module Test_v1.Verify_simple_overrides)
