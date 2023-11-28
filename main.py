import os
from flask import Flask, request, make_response, jsonify, render_template, redirect
from decouple import config
from steam import Steam
from pysteamsignin.steamsignin import SteamSignIn
import requests
from multiprocessing import Pool
import pymongo
from concurrent.futures import ThreadPoolExecutor


app = Flask(__name__, template_folder='html', static_folder='css')

KEY = config("STEAM_API_KEY")
steam = Steam(KEY)

mongodb = pymongo.MongoClient(config("MONGO_URI"))
database = mongodb.game_shifters

def run_in_subprocess(func, process_count=8):
    with Pool(process_count) as pool:
        pool.map(func, range(process_count))


def get_avatar_url(steam_id):
    return steam.users.get_user_details(steam_id)['player']['avatarfull']


def update_top_games():
    recommended_games = []
    recommended_games_json = requests.get('https://steamspy.com/api.php?request=top100in2weeks').json()
    game_ids = list(recommended_games_json.keys())

    for game_id in game_ids[:26]:
        game_data = get_app_data(game_id)
        if game_data:
            recommended_games.append(game_data)

    database.top_games.drop()
    database.top_games.insert_many(recommended_games)

@app.route('/')
def main():
    steam_id = request.cookies.get('steam_id')


    if not steam_id:
        return render_template('index.html')

    # TODO: Run periodically
    update_top_games()

    top_games = list(database.top_games.find({}))

    user = steam.users.get_user_details(steam_id)['player']
    return render_template(
        'index_logged_in.html',
        username=user['personaname'],
        avatar=user['avatarfull'],
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

def get_app_data(app_id):
    try:
        app = database.apps.find_one({'app_id': app_id})
        if app:
            return app
        
        steamspy_data = requests.get(f'https://steamspy.com/api.php?request=appdetails&appid={app_id}').json()
        score = steamspy_data['positive'] / (steamspy_data['positive'] + steamspy_data['negative']) * 10

        app_data = None
        app_data_response = requests.get(f'http://store.steampowered.com/api/appdetails?appids={app_id}&lang=en').json()

        if app_data_response[str(app_id)]['success']:
            app_data = app_data_response[str(app_id)]['data']

        app_data = {
            'app_id': app_id,
            'name': app_data.get('name', None),
            'rating': round(score, 2),
            'required_age': app_data.get('required_age', None),
            'short_description': app_data.get('short_description', None),
            'detailed_description': app_data.get('detailed_description', None),
            'header_image': app_data.get('header_image', None),
            'video': app_data.get('games', [{}])[0].get('webm', {}).get('480', None),
            'website': app_data.get('website', None),
            'pc_requirements': app_data.get('pc_requirements', None),
            'developers': app_data.get('developers', None),
            'metacritic': app_data.get('metacritic', None),
            'genres': app_data.get('genres', None),
            'release_date': app_data.get('release_date', {}).get('date', None),
            'background': app_data.get('background', None),
            'notes': app_data.get('notes', None),
            'offers': 0,
        }
        database.apps.insert_one(app_data)
        return app_data
    except Exception as e:
        print(e)
        return None

def update_user_data(steam_id):
    user_data = database.users.find_one({'steam_id': steam_id})

    user = steam.users.get_user_details(steam_id)['player']
    steam_level = steam.users.get_user_steam_level(steam_id)
    owned_games = steam.users.get_owned_games(steam_id)['games']

    ids = [game['appid'] for game in owned_games]

    with ThreadPoolExecutor() as executor:
        # Use executor.map to parallelize the get_app_data calls
        games = list(executor.map(get_app_data, ids))
    
    if user_data is None:
        database.users.insert_one({
            'steam_id': steam_id,
            'username': user['personaname'],
            'avatar': user['avatarfull'],
            'steam_level': steam_level['player_level'],
            'total_rating': 0,
            'rating_count': 0,
            'star_ratings': [0, 0, 0, 0, 0],
            'comments': [],
            'games': games
        })
    else:
        database.users.update_one(
            {'steam_id': steam_id},
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
    steam_id = steamLogin.ValidateResults(returnData)

    if not steam_id:
        return 'Failed to log in'

    # TODO: Run in subprocess
    update_user_data(steam_id)

    response = make_response(redirect('/'))
    response.set_cookie('steam_id', steam_id, secure=True)
    return response

@app.route('/search')
def search():
    query = request.args.get('q')
    if query is None:
        query = ''

    # IMPORTANT: I have edited the steam library
    res = steam.apps.search_games(query)['apps']
    ids = [r['id'] for r in res]

    games = []
    with ThreadPoolExecutor() as executor:
        # Use executor.map to parallelize the get_app_data calls
        game_data_list = list(executor.map(get_app_data, ids))

    for game_data in game_data_list:
        if game_data:
            games.append(game_data)

    steam_id = request.cookies.get('steam_id')
    return render_template(
        'search.html',
        avatar=get_avatar_url(steam_id),
        query=query,
        games=games,
    )

@app.route('/get_owned_games')
def get_owned_games():
    steam_id = request.cookies.get('steam_id')

    if steam_id is not None:
        owned_games = steam.users.get_owned_games(steam_id)
        return jsonify({"owned_games": owned_games})
    else:
        return 'Please <a href="/?login=true">log in</a>'

@app.route('/delete_account')
def delete_account():
    steam_id = request.cookies.get('steam_id')

    if steam_id is not None:
        database.users.delete_one({'steam_id': steam_id})
        response = make_response(redirect('/'))
        response.set_cookie('steam_id', '', expires=0)
        return response
    else:
        return 'Please <a href="/?login=true">log in</a>'

@app.route('/my_account')
def my_account():
    steam_id = request.cookies.get('steam_id')

    if steam_id is not None:
        user_data = database.users.find_one({'steam_id': steam_id})

        average_rating = round(user_data['total_rating'] / user_data['rating_count'], 2) if user_data['rating_count'] > 0 else 0

        return render_template(
            'account.html',
            username=user_data['username'],
            avatar=user_data['avatar'],
            steam_level=user_data['steam_level'],
            games=user_data['games'],
            ratings=user_data['star_ratings'],
            total_rating=user_data['total_rating'],
            rating_count=user_data['rating_count'] or 1,
            average_rating=average_rating,
            stars_count=round(average_rating),
            comments=user_data['comments']
        )

@app.route('/game')
def game():
    steam_id = request.cookies.get('steam_id')
    game_id = request.args.get('id')

    if game_id is None:
        raise Exception('PAGE NOT FOUND')
    
    game_data = get_app_data(game_id)
    return render_template(
        'game.html',
        avatar=get_avatar_url(steam_id),
        game=game_data
    )
    

if __name__ == '__main__':
    app.run(port=80, host="127.0.0.1", debug=True) 


