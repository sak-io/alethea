---
id: setup
name: Setup Team (meta)
summary: The agent that builds your agent team: interviews you, recommends and installs the right team for your tool, and customizes it.
fits: []
roles: [team-builder]
optional: []
workflows: [build-my-team, customize-team, discover, explain]
---

# Playbook: Setup Team

Used on its own, before any project team exists. `team-builder` interviews you (idea → software type → AI tool → experience), recommends a lean and a full team, installs them with `autocraft init`, explains how to use them in your tool, and later changes them with `autocraft sync`. Everything it does is plain files you can read.
