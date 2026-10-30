from django.urls import path

from . import views

app_name = "planner"

urlpatterns = [
    path("", views.IndexView.as_view(), name="index"),
    path("api/v1/route/", views.RouteView.as_view(), name="route"),
    path("api/v1/route/map/", views.route_map, name="route-map"),
    path("api/v1/health/", views.HealthView.as_view(), name="health"),
]
