import json

from django.test import SimpleTestCase

from ..parsing import ParseError, parse_solution


def valid(**overrides) -> dict:
    data = {
        "problem_summary": "Can all courses be finished?",
        "recognition_clue": ["prerequisites form a graph"],
        "approaches": [
            {
                "name": "BFS (Kahn)",
                "code_ref": "BfsTopologicalSort",
                "core_idea": ["peel off zero indegree nodes"],
                "trap": ["forgetting to decrement the course counter"],
                "complexity_time": "O(V + E)",
                "complexity_space": "O(V + E)",
            }
        ],
        "takeaway": ["cycle detection == topological sort"],
    }
    data.update(overrides)
    return data


class ParseSolutionTests(SimpleTestCase):
    def test_bare_json(self):
        note = parse_solution(json.dumps(valid()))
        self.assertEqual(note.approaches[0].name, "BFS (Kahn)")

    def test_fenced_json(self):
        text = "```json\n" + json.dumps(valid()) + "\n```"
        self.assertEqual(parse_solution(text).schema_version, 1)

    def test_prose_around_json(self):
        text = "Sure! Here you go:\n" + json.dumps(valid()) + "\nHope that helps."
        self.assertEqual(
            parse_solution(text).problem_summary, "Can all courses be finished?"
        )

    def test_braces_inside_strings(self):
        data = valid(takeaway=["use `if (x) { return; }` early exits"])
        self.assertIn("{ return; }", parse_solution(json.dumps(data)).takeaway[0])

    def test_string_coerced_to_list_and_blanks_dropped(self):
        data = valid(recognition_clue="just one clue")
        data["approaches"][0]["trap"] = ["  padded  ", "", "   "]
        note = parse_solution(json.dumps(data))
        self.assertEqual(note.recognition_clue, ["just one clue"])
        self.assertEqual(note.approaches[0].trap, ["padded"])

    def test_extra_keys_ignored(self):
        note = parse_solution(json.dumps(valid(confidence="high")))
        self.assertFalse(hasattr(note, "confidence"))

    def test_missing_field_is_named_in_error(self):
        data = valid()
        del data["approaches"][0]["complexity_time"]
        with self.assertRaises(ParseError) as ctx:
            parse_solution(json.dumps(data))
        self.assertIn("approaches.0.complexity_time", str(ctx.exception))

    def test_empty_approaches_rejected(self):
        with self.assertRaises(ParseError):
            parse_solution(json.dumps(valid(approaches=[])))

    def test_no_json_at_all(self):
        with self.assertRaisesMessage(ParseError, "No JSON object"):
            parse_solution("I could not solve this, sorry.")

    def test_later_valid_object_wins_over_earlier_junk(self):
        text = '{"example": 1}\n' + json.dumps(valid())
        self.assertEqual(parse_solution(text).approaches[0].name, "BFS (Kahn)")

    def test_bullet_markers_stripped(self):
        data = valid(takeaway=["- first", "* second", "3. third", "-1 stays"])
        self.assertEqual(
            parse_solution(json.dumps(data)).takeaway,
            ["first", "second", "third", "-1 stays"],
        )

    def test_complexity_backticks_stripped(self):
        data = valid()
        data["approaches"][0]["complexity_time"] = "`O(n)`"
        self.assertEqual(
            parse_solution(json.dumps(data)).approaches[0].complexity_time, "O(n)"
        )

    def test_name_is_one_line_without_pipes(self):
        data = valid()
        data["approaches"][0]["name"] = "BFS | Kahn\nalgorithm"
        self.assertEqual(
            parse_solution(json.dumps(data)).approaches[0].name, "BFS / Kahn algorithm"
        )
