from django.urls import path
from .views import PodcastSubscriptionAPIView

urlpatterns = [
    path('podcasts/', PodcastSubscriptionAPIView.as_view(), name='podcast-subscribe'),
]