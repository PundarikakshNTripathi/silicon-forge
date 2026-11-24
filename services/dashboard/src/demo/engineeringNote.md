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

The newest agent results come first. I wrote two sets of 13 new tasks and froze them before testing the change each one was meant to check. On the earlier set the change was making the agent ask a question when a request is vague ("make the design faster") instead of guessing. My loop solved 92.3% against 76.9% for LangGraph and 75.4% for Pydantic AI, at about 60% fewer tokens per solved task. Thirteen tasks cannot separate that from zero, and the whole lead came from three vague requests where neither framework asked once in 15 runs; on the other ten they match or beat me. Against my own earlier loop the gain is clear (69.2% to 92.3%).

On the later set, written before I fixed an answer-format bug, it is a tie: 81.5% for my loop, 76.9% for LangGraph, 80.0% for Pydantic AI, nothing separated. The tokens saving held (about 35 to 40%), and the set showed three things the loop still does worse: an answer written at the end of a sentence, renaming a module by creating a new file, and miscounting lines in a log. So I can say the loop got better and cheaper. I cannot say it is ahead of the frameworks.

That came after a first comparison where I lost. On 13 public tasks, repeated three times, LangGraph and Pydantic AI solved about 92% and my loop about 71%. Most of the gap was two fixable problems (an ignored answer format, and file edits that missed their target). I fixed both and reached 92% on the same tasks, but I had read those failures first, so I treat that number as flattering.

My guard code (secret redaction and fencing of tool output) removed the two leaks each framework showed in 20 runs. Those are small numbers, a signal and not a rate.

On VerilogEval, Qwen3.5 9B passes 47.7% of 155 problems (interval 40.0 to 55.5). Feedback loops made the code compile more often, 92% on a 60-problem subset against 81% on all 155, but did not clearly make it more often correct. Fine-tuning a 1.5B model on its own verified answers moved its held-out pass rate from 14.8% to 22.9%, a gain of 8.1 points (interval 3.7 to 12.7), from one round and 37 training examples.

VerilogEval is close to saturated and strong models have probably seen it, so none of this says much about frontier systems.

One more result, a negative one. On 40 NotSoTiny tasks (a hidden-test benchmark of masked Verilog modules, graded by Yosys equivalence), letting a model repair its code from Yosys error messages changed nothing, and a plain coding agent with the same tools (Claude 6 of 20, Gemini 4 of 20) was not separated from a single reply. My first run of that agent scored 20 of 20 because it had opened the answer file on my disk; I threw it out, hid the file and added a check on every transcript. One run each and wide intervals, so this says "no evidence it helps", not "it does not".

## What I would do next

The question I still have not answered: does gating every stage on a tool verdict beat something simpler? The NotSoTiny run is a first look, and it did not favour the check, because a parse check is too weak to tell the model whether the logic is right. The next experiment is a stronger check (a hidden testbench, formal properties), at a larger scale and with repeats. I decided in advance that if the tool-gated approach does not clearly win, the honest conclusion is that this is a teaching tool and not a better method. Training and reinforcement learning only make sense after a positive result, and the published numbers suggest they add a few points, not a leap.
