from django.contrib import admin
from .models import RehostedPodcast, RehostedEpisode

# This makes your models visible and manageable in the admin panel
admin.site.register(RehostedPodcast)
admin.site.register(RehostedEpisode)