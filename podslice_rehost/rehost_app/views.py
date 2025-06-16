from django.http import JsonResponse

def root_view(request):
    """
    A simple root view to confirm the Django service is running.
    """
    return JsonResponse({"message": "PodSlice-Rehost (Django) service is running."})