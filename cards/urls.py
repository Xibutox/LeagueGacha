from django.urls import path

from . import views

app_name = "cards"

urlpatterns = [
    path("", views.home, name="home"),
    path("booster/<slug:slug>/ouvrir/", views.open_booster_view, name="open_booster"),
    path("portail/<int:pk>/ouvrir/", views.open_portal_view, name="open_portal"),
    path("ouverture/<int:pk>/", views.opening_detail, name="opening"),
    path("collection/", views.collection, name="collection"),
    path("carte/<int:pk>/", views.card_detail, name="card_detail"),
    path("carte/<int:pk>/fusionner/", views.fuse_view, name="fuse"),
    path("carte/<int:pk>/echanger/", views.exchange_view, name="exchange"),
    path("equipes/", views.roster_list, name="rosters"),
    path("classement/", views.ranking, name="ranking"),
    path("equipes/nouvelle/", views.roster_edit, name="roster_new"),
    path("equipes/apercu/", views.roster_preview, name="roster_preview"),
    path("equipes/<int:pk>/", views.roster_edit, name="roster_edit"),
    path("equipes/<int:pk>/supprimer/", views.roster_delete, name="roster_delete"),
]
