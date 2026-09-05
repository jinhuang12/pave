# Decision cases for the proposed guidance

Use these synthetic cases to judge whether the proposed text chooses work that answers the claim. They are proposal tests, not campaign evidence or a new runtime gate.

Read the candidate's three changed references. For each case, state the next action, what existing evidence can be reused, and what the result could establish. Do not use hardware. The facts below are supplied test inputs, not claims about the installed Neuron stack.

## A. Compile planning

An existing compile log attributes most wall time to a pass that expands custom calls. Candidate A removes 20% of scalar instructions but leaves its custom calls unchanged. Candidate B removes redundant custom-call sites while preserving outputs. No timing exists for either candidate. The smallest bucket has no custom calls; the next bucket has the same call composition as the failing bucket at a smaller shape. Choose a pilot and state what it would test.

## B. Parallelism

One installed option limits the thread pool in a scheduling phase. A captured memory profile peaks before that phase. Another option sets how many modules are lowered concurrently, with a private allocation per module. Its parser and call sites are verified at the same binary digest. A developer proposes lowering each option to reduce host memory. Assess both experiments and their required observations.

## C. Quantization formats

Format A uses residual limbs and requires one final scale, floored to a power of two and clamped, before quantizing any limb. Its reference rejects separate limb scales. Format B uses one signed integer code per value. It requires scale = 2**ceil(log2(max_abs / qmax)) to avoid overflow and has no residual limbs. A patch applies a floor-of-log2 seed with format A's fixups to both formats. Review the patch and identify relevant numerical cases.

## D. Traced constants

Path A implements a sorted boundary lookup. Creating the boundary tensor from a Python literal inside forward produces a real-versus-fake tensor error. Path B needs a dense 2-by-3 coefficient matrix; its values are not an ordered boundary table. A developer proposes replacing both constants with a count of scalar comparisons followed by index select. Identify which equivalence claims need proof and how to choose a construction for each path.

## E. Probe scope

Probe A checks an encoder's scalar rounding against a supplied mathematical reference using CPU values. It does not import the runner and claims only that arithmetic property. Probe B claims that the candidate model traces through the deployed runner at TP=8 and BF16. It currently constructs on an ad hoc device at TP=1 and FP32, after a module has frozen the wrong target constant at import. State what each probe must change and what remains outside its claim.

## F. Flag syntax

The same compiler binary digest has a stored help transcript, parsed argument file, and passing controlled probe for an integer option. The candidate changes its value within the established range. A second option appears only in a binary string table and has no established type or effect. State the next work for each option. A bare-argument probe of the second option exits with a parse error, but it has no controls yet.

## G. Count meaning

Report A is a completed JSON artifact whose versioned producer schema defines `compiled_graphs` as the number of compiler invocations. The saved producer code and a controlled sample agree. It contains 37 and has no second printed total. Report B prints `load 37`; a separate report prints `37 kernels`, but their relationship is unknown. Both reports are complete and their collectors have passing controls. Assess what can be claimed from each count.

## H. Kernel lowering

The candidate uses an operation whose saved full micro-kernel compile matches its compiler digest, dtype, shape, and kernel context. A second operation has only a passing trace in its proposed context; its only full compile used another dtype and shape. The development budget permits one small compile now and the existing full candidate acceptance later. Choose what to compile and state the limit of the saved evidence.

## I. Startup clocks

Startup A starts the engine-ready deadline before compilation and rank barriers. Startup B completes cache preparation in a separate process before the engine-ready clock starts; its compilation lock bounds only a waiting rank. Both have measured stage walls, cleanup costs, and an approved total budget. A developer assigns both a strict tracer < lock < barrier < engine-ready timeout ordering. Assess the assignment using what each clock covers.

## J. Acceptance instrument

A registered comparison has a numeric threshold and a tripwire input. Its smoke succeeds on ordinary input and also succeeds on the tripwire. The candidate bundle returns exit zero and has an empty evaluated-threshold list. A developer argues that the new guidance allows existing evidence to be reused and asks to proceed to acceptance. Decide what the evidence supports and identify the relevant existing requirements.
