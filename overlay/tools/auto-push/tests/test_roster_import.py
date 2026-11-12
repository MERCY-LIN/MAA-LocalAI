import json

from autopush.planner import load_roster


def test_load_maa_operbox_callback(tmp_path):
    source = tmp_path / "operbox.json"
    source.write_text(
        json.dumps(
            {
                "details": {
                    "own_opers": [
                        {
                            "name": "圣聆初雪",
                            "own": True,
                            "elite": 2,
                            "level": 90,
                            "mainSkillLevel": 7,
                            "skills": [{"id": "skill_2", "level": 3}],
                            "equips": [{"id": "uniequip_001", "type": "Y", "level": 2}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    roster = load_roster(source)
    operator = roster["圣聆初雪"]
    assert operator.elite == 2
    assert operator.level == 90
    assert operator.skill_level == 10
    assert operator.effective_skill_level(1) == 10
    assert operator.module_level == 2
