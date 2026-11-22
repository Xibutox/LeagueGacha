from django.urls import path

from . import quiz_views, tournament_views, views

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
    path("echange/<int:pk>/", views.exchange_offer, name="exchange_offer"),
    path("echange/<int:pk>/choisir/", views.choose_exchange_view, name="choose_exchange"),
    path("equipes/", views.roster_list, name="rosters"),
    path("classement/", views.ranking, name="ranking"),
    path("quiz/", quiz_views.quiz_page, name="quiz"),
    path("quiz/commencer/", quiz_views.quiz_start, name="quiz_start"),
    path("quiz/<int:pk>/repondre/", quiz_views.quiz_answer, name="quiz_answer"),
    path("quiz/<int:pk>/choisir/", quiz_views.quiz_choose, name="quiz_choose"),
    path("tournoi/", tournament_views.tournament_home, name="tournament"),
    path("tournoi/<int:pk>/defier/", tournament_views.tournament_start, name="tournament_start"),
    path("tournoi/serie/<int:pk>/", tournament_views.tournament_series, name="tournament_series"),
    path("tournoi/serie/<int:pk>/coup/", tournament_views.tournament_action, name="tournament_action"),
    path("equipes/nouvelle/", views.roster_edit, name="roster_new"),
    path("equipes/apercu/", views.roster_preview, name="roster_preview"),
    path("equipes/<int:pk>/", views.roster_edit, name="roster_edit"),
    path("equipes/<int:pk>/supprimer/", views.roster_delete, name="roster_delete"),
]
