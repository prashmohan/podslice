from django.contrib import admin
from django.urls import path, include

# This is the root URL configuration for the entire podslice_rehost service.
# Its primary job is to delegate routing to the individual apps.
urlpatterns = [
    # The admin site is included for debugging and management purposes.
    path('admin/', admin.site.urls),

    # This is the crucial line. It tells Django that for any URL that comes in,
    # it should look for a match within the URL patterns defined in
    # 'rehost_app.urls'. We don't add a prefix, so the path
    # 'media/episodes/<uuid:media_guid>/' from the app's urls.py will be
    # the final, accessible URL.
    path('', include('rehost_app.urls')),
]