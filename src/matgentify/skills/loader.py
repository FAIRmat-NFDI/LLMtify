"""Agent Skills (SKILL.md folders), all parsed by MAF.

Two ways to use a skill:
  - progressive (agent decides): `skills: true` on an agent -> MAF SkillsProvider
    (agent sees names/descriptions, calls load_skill when needed)
  - grounded (always on): `ground_skills: [tool-finder]` on an agent -> the skill's full
    SKILL.md is in the instructions of every run (SkillGroundingProvider). Executors can also
    read a skill's `metadata:` frontmatter as small config (e.g. fallback rules).
"""

import inspect
from pathlib import Path

from agent_framework import FileSkillsSource, Skill, SkillsProvider, SkillsSourceContext


class SkillsLoader:
    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = Path(skills_dir)
        self._cache: dict[str, Skill] | None = None

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

    async def get(self, name: str) -> Skill:
        """One skill by frontmatter name, parsed by MAF FileSkillsSource (cached)."""
        if self._cache is None:
            skills = await FileSkillsSource(str(self.skills_dir)).get_skills(SkillsSourceContext(agent=None))
            self._cache = {s.frontmatter.name: s for s in skills}
        if name not in self._cache:
            raise KeyError(f"skill '{name}' not found in {self.skills_dir} (have {sorted(self._cache)})")
        return self._cache[name]

    async def content(self, name: str) -> str:
        content = (await self.get(name)).get_content()
        return await content if inspect.isawaitable(content) else content

    async def metadata(self, name: str) -> dict[str, str]:
        return dict((await self.get(name)).frontmatter.metadata or {})
