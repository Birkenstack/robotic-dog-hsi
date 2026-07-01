# Petoi Bittle X Research Log

## Proposal Context

This project implements a browser-based AI literacy tool for middle-school students using the Petoi Bittle X robotic dog. The proposal emphasizes:

- embodied AI learning through a physical robotic agent
- accessible multimodal interaction
- prompt engineering and algorithmic thinking
- classroom-friendly deployment and evaluation

## Current Implementation Direction

The original code allowed the language model to generate Petoi serial commands directly. That is technically workable, but it is weak as a research contribution because the reasoning process is hidden, command generation is hard to evaluate, and safety depends on a small blocklist.

The revised direction is:

1. Interpret user intent at a higher level.
2. Map that intent to a constrained skill library in application code.
3. Validate every generated command before execution.
4. Return an explanation trace that shows how language became action.

This makes the system safer, more explainable, and easier to study in a classroom setting.

## Implementation Decisions And Justifications

### 1. Constrained Skill Planner

Decision:
- Replace free-form LLM serial generation with a constrained skill library plus planner.

Why:
- Students should be able to inspect how a prompt is transformed into an action.
- A fixed skill vocabulary supports replicable evaluation.
- Grounding language in allowed actions is more reliable than letting the model invent low-level control.

Sources:
- Touretzky et al., "Envisioning AI for K-12: What Should Every Child Know about AI?"
  https://ojs.aaai.org/index.php/AAAI/article/download/5053/4926
- Hunt, Ramchurn, and Soorati, "A Survey of Language-Based Communication in Robotics"
  https://arxiv.org/abs/2406.04086
- Ahn et al., "Do As I Can, Not As I Say: Grounding Language in Robotic Affordances"
  https://arxiv.org/abs/2204.01691

### 2. Explainable Trace Output

Decision:
- Return intent, planner source, rationale, and selected skill along with robot commands.

Why:
- The proposal focuses on AI literacy, not just robot actuation.
- Students and researchers need to see the intermediate reasoning step.
- Trace output supports debugging, classroom discussion, and post-study analysis.

Sources:
- Casal-Otero et al., "AI literacy in K-12: a systematic literature review"
  https://link.springer.com/article/10.1186/s40594-023-00418-7
- Yim and Su, "Artificial intelligence (AI) learning tools in K-12 education: A scoping review"
  https://link.springer.com/article/10.1007/s40692-023-00304-9

### 3. Stronger Command Validation

Decision:
- Validate command shape, skill membership, joint ranges, and sequence length in server code.

Why:
- Middle-school classroom use requires stronger runtime safety than a prompt-only policy.
- Validated command output is easier to justify in a research paper than "the model usually behaves."

Sources:
- Hunt, Ramchurn, and Soorati, "A Survey of Language-Based Communication in Robotics"
  https://arxiv.org/abs/2406.04086
- Williams et al., "AI + Ethics Curricula for Middle School Youth: Lessons Learned from Three Project-Based Curricula"
  https://pmc.ncbi.nlm.nih.gov/articles/PMC9342939/

### 4. Research Logging

Decision:
- Preserve structured planning and execution metadata in API responses so it can be logged later.
- Persist interaction records as JSONL during runtime for later quantitative and qualitative analysis.

Why:
- Evaluation in the proposal depends on qualitative and quantitative analysis.
- The system should support later measurement of interpretation quality, execution success, and student rephrasing behavior.
- JSONL logs make replay, audit, and coding of classroom sessions straightforward without requiring the robot to be powered on again.

Sources:
- Zhang, Lee, and Moore, "An Effectiveness Study of Teacher-Led AI Literacy Curriculum in K-12 Classrooms"
  https://ojs.aaai.org/index.php/AAAI/article/view/30380
- Ouyang and Xu, "The effects of educational robotics in STEM education: a multilevel meta-analysis"
  https://link.springer.com/article/10.1186/s40594-024-00469-4

## Useful Comparison Repositories

- OpenCat quadruped platform:
  https://github.com/PetoiCamp/OpenCat-Quadruped-Robot
- ROS2 integration for Bittle:
  https://github.com/gravesreid/bittle_ros2
- Mini Pupper open-source robot dog:
  https://github.com/Tiryoh/MiniPupper-QuadrupedRobot
- Natural-language quadruped interaction:
  https://github.com/gaurang-1402/chatpuppy

## Practical Next Steps

- add student-safe and teacher-debug execution modes
- narrow the visible command set to a smaller classroom vocabulary
- add replay mode so interactions can be analyzed without the robot powered on
