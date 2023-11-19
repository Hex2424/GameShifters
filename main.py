import os
from flask import Flask, request, make_response, jsonify, render_template, redirect
from decouple import config
from steam import Steam
from pysteamsignin.steamsignin import SteamSignIn
import requests
from multiprocessing import Pool
import pymongo

app = Flask(__name__, template_folder='html', static_folder='css')

KEY = config("STEAM_API_KEY")
steam = Steam(KEY)

mongodb = pymongo.MongoClient(config("MONGO_URI"))
database = mongodb.game_shifters

@app.route('/')
def main():
    steamID = request.cookies.get('steam_id')

    # recommended_games = []
    # recommended_games_json = requests.get('https://steamspy.com/api.php?request=top100in2weeks').json()
    # game_ids = list(recommended_games_json.keys())
    # games_data = list(recommended_games_json.values())

    # for game_id, data in zip(game_ids[:25], games_data[:25]):
    #     game_data = requests.get(f'https://store.steampowered.com/api/appdetails?appids={game_id}&lang=en').json()[game_id]['data']

    #     score = data['positive'] / (data['positive'] + data['negative']) * 10
    #     game = {
    #         'name': game_data['name'],
    #         'img': game_data['header_image'],
    #         'rating': score.__round__(2),
    #         'website': f'https://store.steampowered.com/agecheck/app/{game_id}/'
    #     }
    #     recommended_games.append(game)

    recommended_games = list(database.top_games.find({}))
    top_games = recommended_games

    if not steamID:
        return render_template(
            'index.html',
            recommended_games=recommended_games,
            top_games=top_games,
        )

    user = steam.users.get_user_details(steamID)['player']
    return render_template(
        'index_logged_in.html',
        username=user['personaname'],
        avatar=user['avatarfull'],
        recommended_games=recommended_games,
        top_games=top_games
    )

@app.route('/login')
def login():
    steamLogin = SteamSignIn()
    return steamLogin.RedirectUser(steamLogin.ConstructURL('http://localhost:80/processlogin'))

@app.route('/logout')
def logout():
    response = make_response(redirect('/'))
    response.set_cookie('steam_id', '', expires=0)
    return response

def update_user_data(steamID):
    user_data = database.users.find_one({'steam_id': steamID})

    user = steam.users.get_user_details(steamID)['player']
    steam_level = steam.users.get_user_steam_level(steamID)
    owned_games = steam.users.get_owned_games(steamID)['games']

    games = []
    for owned_game in owned_games:
        app_id = owned_game['appid']

        try:
            game_data = requests.get(f'https://store.steampowered.com/api/appdetails?appids={app_id}&lang=en').json()[str(app_id)]['data']
        except:
            continue

        game = {
            'name': game_data['name'],
            'img': game_data['header_image'],
            'website': f'https://store.steampowered.com/agecheck/app/{app_id}/'
        }
        games.append(game)
    
    if user_data is None:
        database.users.insert_one({
            'steam_id': steamID,
            'username': user['personaname'],
            'avatar': user['avatarfull'],
            'steam_level': steam_level['player_level'],
            'games': games
        })
    else:
        database.users.update_one(
            {'steam_id': steamID},
            {'$set': {
                'username': user['personaname'],
                'avatar': user['avatarfull'],
                'steam_level': steam_level['player_level'],
                'games': games
            }}
        )

@app.route('/processlogin')
def process():
    returnData = request.values
    steamLogin = SteamSignIn()
    steamID = steamLogin.ValidateResults(returnData)

    if not steamID:
        return 'Failed to log in'
    
    update_user_data(steamID)

    response = make_response(redirect('/'))
    response.set_cookie('steam_id', steamID, secure=True)
    return response

@app.route('/search')
def search():
    query = request.args.get('q')
    if query is None:
        return 'No query provided'
    else:
        res = steam.apps.search_games(query)
        return 'Found {0} results for {1}'.format(len(res), query)

@app.route('/get_owned_games')
def get_owned_games():
    steamID = request.cookies.get('steam_id')

    if steamID is not None:
        owned_games = steam.users.get_owned_games(steamID)
        return jsonify({"owned_games": owned_games})
    else:
        return 'Please <a href="/?login=true">log in</a>'

@app.route('/my_account')
def my_account():
    steamID = request.cookies.get('steam_id')

    if steamID is not None:
        user_data = database.users.find_one({'steam_id': steamID})

        return render_template(
            'account.html',
            username=user_data['username'],
            avatar=user_data['avatar'],
            steam_level=user_data['steam_level'],
            games=user_data['games'],
        )

if __name__ == '__main__':
    app.run(port=80, host="127.0.0.1", debug=True) 


