"""The presenter skills' instructions must point at files and commands that exist, so a move or
rename cannot silently leave an agent following a dead path."""

from pathlib import Path
import re
import unittest

from typer.main import get_command

from presenter_tts.cli import app

SKILLS = Path(__file__).resolve().parents[3]
PRESENTER, SETUP = SKILLS / "presenter", SKILLS / "presenter-setup"


def frontmatter(skill):
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    block = text.split("---", 2)[1]
    return {k: v.strip() for k, v in re.findall(r"^([a-z-]+):(.*)$", block, re.M)}, text


class SkillLayoutTests(unittest.TestCase):
    def test_frontmatter_follows_the_agent_skills_spec(self):
        for skill in (PRESENTER, SETUP):
            meta, _ = frontmatter(skill)
            self.assertEqual(meta["name"], skill.name)
            self.assertLessEqual(len(meta["description"]), 1024)
            self.assertLessEqual(len(meta["compatibility"]), 500)

    def test_referenced_files_exist(self):
        for skill in (PRESENTER, SETUP):
            texts = [(skill / "SKILL.md")] + sorted((skill / "references").glob("*.md"))
            for path in texts:
                body = path.read_text(encoding="utf-8")
                links = re.findall(r"\]\((?!https?:)([^)#]+)", body)
                bundled = re.findall(r"<presenter>/(tools/[\w./-]+)", body)
                for link in links:
                    self.assertTrue((path.parent / link).exists(), f"{path.name}: {link}")
                for rel in bundled:
                    self.assertTrue((PRESENTER / rel.rstrip("/.")).exists(), f"{path.name}: {rel}")

    def test_documented_commands_exist(self):
        commands = get_command(app).commands
        names = set(commands) | {f"{group} {sub}" for group, cmd in commands.items()
                                 if hasattr(cmd, "commands") for sub in cmd.commands}
        for skill in (PRESENTER, SETUP):
            for path in [skill / "SKILL.md", *sorted((skill / "references").glob("*.md"))]:
                for used in re.findall(r"presenter-tts ([a-z]+(?: [a-z]+)?)", path.read_text(encoding="utf-8")):
                    first = used.split()[0]
                    self.assertTrue(used in names or first in names, f"{path.name}: presenter-tts {used}")


if __name__ == "__main__":
    unittest.main()
