# Scene-Aware Reasoning Experiment

## Research question

Can a language-model planner ground a natural-language robot command in
evidence from a fixed webcam while keeping the resulting actions constrained,
inspectable, and safe?

## Stage 1: reasoning preview

This branch adds a deliberately non-executing prototype:

1. The user starts the existing browser camera.
2. The user enables **Ground this command in the camera view**.
3. The browser captures one compressed JPEG when a command is submitted.
4. The server sends the command and image to a configured vision-capable model.
5. The model returns structured observations, robot-pose uncertainty, command
   grounding, proposed allowlisted skills, and a verification target.
6. The interface displays this evidence alongside the existing planner trace.

The `/api/scene-reasoning` endpoint does not call the serial manager. It returns
`commands: []`, reports any candidate commands separately as
`proposed_commands`, and logs `executed: false`. Enabling scene context also
pauses the browser's older face-tracking servo commands and blocks direct
serial input until scene context is turned off.

## Suggested evaluation setup

- Use a fixed overhead or elevated webcam.
- Start with an uncluttered, evenly lit floor area.
- Try a yellow-and-black Bittle, a red cup, a blue or green ball, a cardboard
  box, and a notebook or colored target.
- Repeat each prompt with small changes in lighting, camera angle, and object
  placement.
- Record whether the robot, target, obstacle, and heading were described
  correctly and whether uncertainty was stated honestly.

Example prompts:

- `What is between the robot and the red cup?`
- `Plan how the robot could face the blue ball.`
- `Could the robot safely move toward the notebook?`
- `What would need to be verified before walking forward?`

## Evidence to inspect

Each preview writes a `scene_reasoning_preview` row to the configured interaction
log. The row contains the reported scene evidence, proposed skills and commands,
latency, model name, and an explicit `executed: false` value. Camera image data
is not written to the log.

## Growth path

1. Evaluate snapshot reasoning and failure cases.
2. Add repeat captures to compare before/after frames.
3. Add removable pose markers only if natural robot-heading recognition is not
   reliable enough.
4. Permit one short allowlisted action after explicit confirmation.
5. Re-observe and stop or replan after every action.

Continuous autonomous execution is intentionally outside Stage 1.
