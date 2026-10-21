# Customizing your agent team

Edit things here, then run `python3 .autocraft/framework/bin/autocraft sync`. Never edit generated files directly.

- `team-rules.md` - rules for ALL roles in this project.
- `roles/<id>.append.md` - extra rules for one role (appended).
- `roles/<id>.md` - a full replacement (or a brand-new role; create with `autocraft new-role <id>`).
- `workflows/<id>.md` - extra or replacement workflows.
- `../config.yml` - team, roles_add / roles_remove, tools, archetypes, capabilities, experience, limits.
