from django.urls import path
from .views import serve_rehosted_media

# This defines the URL patterns for the rehost_app.
# It is best practice to give a name to each URL pattern.
urlpatterns = [
    # This pattern is designed to match URLs that contain a UUID.
    # Example: /media/episodes/a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d/
    # The <uuid:media_guid> part captures the UUID from the URL and passes it
    # as a keyword argument named 'media_guid' to our view function.
    path('media/episodes/<uuid:media_guid>/', serve_rehosted_media, name='serve_media_episode'),
]