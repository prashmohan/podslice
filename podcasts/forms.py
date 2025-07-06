from django import forms

class OPMLImportForm(forms.Form):
    opml_file = forms.FileField()
