from django.urls import path
from . import views

urlpatterns = [
    path('', views.root_view, name='root'),
]