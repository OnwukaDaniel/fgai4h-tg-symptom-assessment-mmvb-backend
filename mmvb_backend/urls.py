"""mmvb_backend URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView
from rest_framework.schemas import get_schema_view

from ai_implementations.api.urls import router as ai_implementations_router
from benchmarking_sessions.api.urls import (
    router as benchmarking_sessions_router,
)
from cases.api.urls import router as cases_router
from common.routers import DefaultRouter
from metrics.api.urls import router as metrics_router

router = DefaultRouter(trailing_slash=False)
router.extend(ai_implementations_router)
router.extend(benchmarking_sessions_router)
router.extend(cases_router)
router.extend(metrics_router)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include(router.urls)),
    path(
        "openapi/",
        get_schema_view(
            title="WHO-ITU AI Benchmarking", description="", version="v1"
        ),
        name="openapi-schema",
    ),
    path(
        "api/docs/",
        TemplateView.as_view(
            template_name="swagger-ui.html",
            extra_context={"schema_url": "openapi-schema"},
        ),
        name="swagger-ui",
    ),
]

if settings.ENABLE_TOY_AIS:
    urlpatterns.append(path("toy_ais/", include("toy_ais.urls")),)
