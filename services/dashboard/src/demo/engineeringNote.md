# Engineering note

## What this is

SiliconForge is my attempt at a question I kept running into: if a language model writes hardware, who checks it? My answer is that the model never gets the last word. It proposes a design, and open-source tools (Yosys, SymbiYosys, Verilator, OpenROAD) accept or reject it. A formal proof either holds or it does not. A timing number is above zero or below it. Whatever the tools say goes back to the model as a short, specific message, and it tries again.

It runs on a laptop. The model I used most is a local one, Qwen3.5 9B in 4-bit form, on an 8 GB graphics card. I wanted to know how far a small model gets when the checking around it is strict.

## What I built

- A pipeline from a written spec to a finished layout: a design contract, the RTL, lint-style guardrails, formal proofs, an equivalence check against the synthesised netlist, place and route, and signoff. Every result carries a stamp saying which tool produced it, so a stand-in can never pass as the real thing.
- An agent that works inside a folder you choose. It asks before it changes a file, redacts secrets, and compacts its own history so a small model's context does not overflow.
- A benchmark that pits my own agent loop against LangGraph and Pydantic AI: same model, same tools, same tasks, same measurements.
- An evaluation harness that reports intervals instead of single numbers, plus one round of fine-tuning on the model's own verified answers.
- This demo. Everything in it is a recording of those results. Nothing runs live.

## What I measured, and what the numbers do not show

On 13 tasks, five trials each, repeated three times, LangGraph and Pydantic AI solved about 92% and my loop about 71%. Most of that gap came from two fixable problems: my loop ignored a requested answer format, and its file edits often missed their target. I fixed both and the loop reached 92% on the same tasks. I wrote those fixes after reading the failures on public tasks, so that figure flatters me and I treat it that way. The frameworks were not re-run. Thirteen tasks and one model are too few to rank anything.

My guard code (secret redaction and fencing of tool output) removed the two leaks each framework showed in 20 runs. Those are small numbers, a signal and not a rate.

On VerilogEval, Qwen3.5 9B passes 47.7% of 155 problems (interval 40.0 to 55.5). Feedback loops made the code compile more often, 92% on a 60-problem subset against 81% on all 155, but did not clearly make it more often correct. Fine-tuning a 1.5B model on its own verified answers moved its held-out pass rate from 14.8% to 22.9%, a gain of 8.1 points (interval 3.7 to 12.7), from one round and 37 training examples.

VerilogEval is close to saturated and strong models have probably seen it, so none of this says much about frontier systems.

## What I would do next

The biggest hole: I have not shown that gating every stage on a tool verdict beats something simpler, such as a plain coding agent that is handed Yosys. That comparison, on tasks the models have not seen and with tests the agent cannot see, comes first. If the pipeline does not clearly win, the honest conclusion is that this is a teaching tool and not a better method, and I would stop there. Training and reinforcement learning only make sense after that result, and the published numbers suggest they add a few points, not a leap.
