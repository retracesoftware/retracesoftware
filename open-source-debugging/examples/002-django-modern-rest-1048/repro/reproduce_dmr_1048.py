import enum
import secrets
import sys

import msgspec
from django.conf import settings
from django.core.management import execute_from_command_line
from django.urls import include

from dmr import Controller, Query
from dmr.openapi import build_schema
from dmr.openapi.views import OpenAPIJsonView, SwaggerView
from dmr.plugins.msgspec import MsgspecSerializer
from dmr.routing import Router, path


if not settings.configured:
    settings.configure(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS='*',
        DEBUG=True,
        INSTALLED_APPS=['dmr', 'django.contrib.staticfiles'],
        STATIC_URL='/static/',
        STATICFILES_FINDERS=[
            'django.contrib.staticfiles.finders.AppDirectoriesFinder',
        ],
        TEMPLATES=[
            {
                'APP_DIRS': True,
                'BACKEND': 'django.template.backends.django.DjangoTemplates',
            },
        ],
        SECRET_KEY=secrets.token_hex(),
    )


class TestEnum(enum.StrEnum):
    NULL = 'None'


class TestQuery(msgspec.Struct, kw_only=True):
    e: TestEnum = TestEnum.NULL


class TestController(Controller[MsgspecSerializer]):
    async def get(self, parsed_query: Query[TestQuery]) -> None:
        return None


router = Router(
    'api/',
    [
        path('test/', TestController.as_view(), name='test'),
    ],
)
schema = build_schema(router)

urlpatterns = [
    path(router.prefix, include((router.urls, 'your_app'), namespace='api')),
    path('docs/openapi.json/', OpenAPIJsonView.as_view(schema), name='openapi'),
    path('docs/swagger/', SwaggerView.as_view(schema), name='swagger'),
]


if __name__ == '__main__':
    execute_from_command_line(sys.argv)
