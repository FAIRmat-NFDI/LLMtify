"""Agent Skills (SKILL.md folders) -> MAF SkillsProvider (a context provider).

Drop a folder into skills/<name>/SKILL.md and any agent with `skills: true` can use it.
"""

from pathlib import Path

from agent_framework import SkillsProvider


class SkillsLoader:
    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = Path(skills_dir)

    def available(self) -> list[str]:
        if not self.skills_dir.exists():
            return []
        return sorted(p.parent.name for p in self.skills_dir.glob("*/SKILL.md"))

    def provider(self) -> SkillsProvider | None:
        if not self.available():
            return None
        # load/read are trusted -> no approval; script execution keeps approval on.
        return SkillsProvider.from_paths(
            skill_paths=str(self.skills_dir),
            disable_load_skill_approval=True,
            disable_read_skill_resource_approval=True,
        )
