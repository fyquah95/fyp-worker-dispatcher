open Core
open Async
open Protocol.Shadow_fyp_compiler_lib
open Patdiff_lib

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
    Async_shell.run ~echo:false ~verbose:false ~expect:[ 0 ] ~working_dir
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
      Inlining_tree.Top_level.uniform_random_mutation tree
    in
    let%bind () = compile_with_decisions modified_tree in
    let%map compiled_tree = load_decision_tree () in

    assert (not (Inlining_tree.Top_level.compare tree modified_tree = 0));
    let soundness =
      Inlining_tree.Top_level.check_soundness
        ~compiled:compiled_tree
        ~reference:modified_tree ()
    in
    if not soundness.is_sound then begin
      let buffer = Buffer.create 1000 in
      Inlining_tree.Top_level.pprint buffer compiled_tree;
      printf "COMPILED:\n%s\n" (Buffer.contents buffer);

      let buffer = Buffer.create 1000 in
      Inlining_tree.Top_level.pprint buffer modified_tree;
      printf "TARGET:\n%s\n" (Buffer.contents buffer);

      let buffer = Buffer.create 1000 in
      Inlining_tree.Top_level.Expanded.pprint buffer
        (Inlining_tree.Top_level.expand modified_tree);
      printf "Expanded tree:\n%s\n" (Buffer.contents buffer);

      assert false;
    end;


    compiled_tree
  ;;

  let compile_with_decisions ~overrides_sexp_of_tree root =
    let echo = true in
    let verbose = true in
    let working_dir = datadir in
    let%bind () =
      Async_shell.run ~echo ~verbose ~expect:[ 0 ] ~working_dir
        "bash" [ "-c"; "make clean" ]
    in
    let%bind () =
      let overrides = overrides_sexp_of_tree root in
      Writer.save_sexp (datadir ^/ "overrides.sexp") overrides
    in
    Async_shell.run ~echo ~verbose ~expect:[ 0 ] ~working_dir
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
      (* printf "Running initial complation\n"; *)
      let%bind () = compile_with_decisions [] in
      let%bind tree = load_decision_tree () in
      Deferred.ignore (
        List.init 30 ~f:Fn.id
        |> Deferred.List.fold ~init:tree ~f:(fun tree iter  ->
            run_single_iter ~iter ~tree ~compile_with_decisions)
      )
  end

  module Verify_v1_expanded_overrides = struct
    let name = "v1.verify_v1_expanded_overrides"

    let overrides_sexp_of_tree root =
      [%sexp_of: Data_collector.Overrides.t] (
        root
        |> Inlining_tree.Top_level.expand
        |> Inlining_tree.Top_level.Expanded.to_override_rules
      )
    ;;

    let compile_with_decisions =
      compile_with_decisions ~overrides_sexp_of_tree
    ;;

    let run () =
      (* printf "Running initial complation\n"; *)
      let%bind () = compile_with_decisions [] in
      let%bind tree = load_decision_tree () in
      Deferred.ignore (
        List.init 30 ~f:Fn.id
        |> Deferred.List.fold ~init:tree ~f:(fun tree iter  ->
            run_single_iter ~iter ~tree ~compile_with_decisions)
      )
  end

  module Verify_simple_overrides = struct
    let name = "v1.verify_simple_overrides"

    let overrides_sexp_of_tree root =
      [%sexp_of: Protocol.Shadow_fyp_compiler_lib.Data_collector.Simple_overrides.t] (
        Inlining_tree.Top_level.to_simple_overrides root
      )
    ;;

    let compile_with_decisions =
      compile_with_decisions ~overrides_sexp_of_tree
    ;;

    let run () =
      (* printf "Running  initial complation\n";*)
      let%bind () = compile_with_decisions [] in
      let%bind tree = load_decision_tree () in
      Deferred.ignore (
        List.init 30 ~f:Fn.id
        |> Deferred.List.fold ~init:tree ~f:(fun tree iter  ->
            run_single_iter ~iter ~tree ~compile_with_decisions)
      )
  end

  module Verify_expansion = struct
    let name = "v1.verify_expansion"

    module E = Inlining_tree.Top_level.Expanded

    let run () =
      List.iteri Expansion_testdata.examples ~f:(fun i (name, input, expected_output) ->
          let obtained = Inlining_tree.Top_level.expand input in
          let eq =
            Inlining_tree.Top_level.Expanded.weak_equal obtained expected_output
          in
          if not eq then begin
            let buffer = Buffer.create 1000 in
            Inlining_tree.Top_level.pprint buffer input;
            printf ">>>>> FAILED at input %s <<<<<\n" name;
            printf "Input tree:\n%s\n" (Buffer.contents buffer);
            let pprint_expanded name expanded =
              let buffer = Buffer.create 1000 in
              E.pprint buffer expanded;
              { Patdiff_core. name; text = Buffer.contents buffer }
            in

            let obtained = pprint_expanded "obtained" obtained in
            let expected = pprint_expanded "expected" expected_output in
            let diff =
              Patdiff_core.patdiff ~keep_ws:true ~from_:expected ~to_:obtained ()
            in
            printf "DIFF:\n%s\n\n\n" diff;

            assert eq
          end
        );
      Deferred.unit
  end
end

let () =
  Testlib.register (module Test_v1.Verify_expansion);
  Testlib.register (module Test_v1.Verify_v1_overrides);
  Testlib.register (module Test_v1.Verify_simple_overrides);
  Testlib.register (module Test_v1.Verify_v1_expanded_overrides)
;;
