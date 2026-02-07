from django import forms
from podcasts.models import Podcast

class OPMLImportForm(forms.Form):
    opml_file = forms.FileField()

class PodcastSettingsForm(forms.ModelForm):
    class Meta:
        model = Podcast
        fields = ['max_episodes']
        widgets = {
            'max_episodes': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
        }
