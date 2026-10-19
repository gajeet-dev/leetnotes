from django import forms

from .parsing import ParseError, parse_solution


class ManualResponseForm(forms.Form):
    label = forms.CharField(
        required=False,
        initial="claude.ai",
        max_length=50,
        help_text="Which AI you used (optional).",
    )
    raw_text = forms.CharField(
        label="AI reply",
        widget=forms.Textarea(
            attrs={"rows": 18, "style": "width: 100%; font-family: monospace;"}
        ),
    )

    def clean_raw_text(self):
        text = self.cleaned_data["raw_text"]
        try:
            parse_solution(text)
        except ParseError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return text
