import datetime
from flask import Flask, Response, request, make_response, jsonify, render_template, redirect
from decouple import config
from steam import Steam
from pysteamsignin.steamsignin import SteamSignIn
import requests
from multiprocessing import Pool
import pymongo
from concurrent.futures import ThreadPoolExecutor
import threading
from bson import ObjectId


app = Flask(__name__, template_folder='html', static_folder='css')

KEY = config("STEAM_API_KEY")
steam = Steam(KEY)

mongodb = pymongo.MongoClient(config("MONGO_URI"))
database = mongodb.game_shifters

def format_time_ago(message_timestamp):
    current_time = datetime.datetime.now()
    time_difference = current_time - message_timestamp

    if time_difference < datetime.timedelta(minutes=1):
        return 'just now'
    elif time_difference < datetime.timedelta(hours=1):
        minutes = int(time_difference.total_seconds() / 60)
        return f'{minutes} minute ago' if minutes == 1 else f'{minutes} minutes ago'
    elif time_difference < datetime.timedelta(days=1):
        hours = int(time_difference.total_seconds() / 3600)
        return f'{hours} hour ago' if hours == 1 else f'{hours} hours ago'
    elif time_difference < datetime.timedelta(days=7):
        days = int(time_difference.total_seconds() / 86400)
        return f'{days} day ago' if days == 1 else f'{days} days ago'
    elif time_difference < datetime.timedelta(days=30):
        weeks = int(time_difference.total_seconds() / 604800)
        return f'{weeks} week ago' if weeks == 1 else f'{weeks} weeks ago'
    elif time_difference < datetime.timedelta(days=365):
        months = int(time_difference.total_seconds() / 2592000)
        return f'{months} month ago' if months == 1 else f'{months} months ago'
    else:
        years = int(time_difference.total_seconds() / 31536000)
        return f'{years} year ago' if years == 1 else f'{years} years ago'

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

def get_game_owners(game_id):
    users = database.users.aggregate([
        {
            '$match': {
                'games.app_id': int(game_id)
            }
        }, {
            '$project': {
                '_id': 1, 
                'steam_id': 1,
                'username': 1, 
                'avatar': 1,
            }
        }
    ])
    users = list(users)
    return users

def get_app_data(app_id):
    try:
        app = database.apps.find_one({'app_id': app_id})
        users = get_game_owners(app_id)

        if app:
            app['offers'] = len(users)
            app['users'] = users
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
        }
        database.apps.insert_one(app_data)
        app_data['offers'] = len(users)
        app_data['users'] = users
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

    # remove None values
    games = [game for game in games if game]
    
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
            rating_count=user_data['rating_count'],
            average_rating=average_rating,
            stars_count=round(average_rating),
            comments=user_data['comments']
        )


@app.route('/user')
def user():
    steam_id = request.cookies.get('steam_id')
    user_id = request.args.get('id')

    if user_id is None:
        return 'User not found'
    
    active_user = database.users.find_one({'steam_id': steam_id})

    user_data = database.users.find_one({'steam_id': user_id})

    average_rating = round(user_data['total_rating'] / user_data['rating_count'], 2) if user_data['rating_count'] > 0 else 0

    user_trades = database.trades.aggregate([
            {
                '$match': {
                    '$or': [
                        {
                            'initiator_id': user_id, 
                            'user_id': steam_id,
                            'completed': True,
                            'user_rated': False
                        }, {
                            'user_id': steam_id, 
                            'user_id': user_id,
                            'completed': True,
                            'initiator_rated': False
                        }
                    ]
                }
            }, {
                '$project': {
                    'initiator_rated': 0, 
                    'user_rated': 0, 
                    'user_id': 0, 
                    'completed': 0
                }
            }
        ])
    allowed_to_rate = bool(list(user_trades))

    return render_template(
        'user.html',
        active_user_avatar=active_user['avatar'],
        username=user_data['username'],
        avatar=user_data['avatar'],
        steam_level=user_data['steam_level'],
        games=user_data['games'],
        ratings=user_data['star_ratings'],
        total_rating=user_data['total_rating'],
        rating_count=user_data['rating_count'],
        average_rating=average_rating,
        stars_count=round(average_rating),
        comments=user_data['comments'],
        user_id=user_id,
        allowed_to_rate=allowed_to_rate,
    )

@app.route('/rate_user', methods=['POST'])
def rate_user():
    try:
        steam_id = request.cookies.get('steam_id')

        if steam_id is None:
            return Response('Not logged in', status=401)

        active_user = database.users.find_one({'steam_id': steam_id})

        user_id = request.json['user_id']
        rating = int(request.json['rating'])
        comment = request.json['comment']

        user_trades = database.trades.aggregate([
            {
                '$match': {
                    '$or': [
                        {
                            'initiator_id': user_id, 
                            'user_id': steam_id,
                            'completed': True,
                            'user_rated': False
                        }, {
                            'user_id': steam_id, 
                            'user_id': user_id,
                            'completed': True,
                            'initiator_rated': False
                        }
                    ]
                }
            }, {
                '$project': {
                    'initiator_rated': 0, 
                    'user_rated': 0, 
                    'user_id': 0, 
                    'completed': 0
                }
            }
        ])
        user_trades = list(user_trades)
        allowed_to_rate = bool(user_trades)

        if not allowed_to_rate:
            return Response('You have no permission to rate this user', status=403)

        user = database.users.find_one({'steam_id': user_id})

        user['total_rating'] += rating
        user['rating_count'] += 1
        user['star_ratings'][rating - 1] += 1
        user['comments'].append({
            'author': active_user['username'],
            'content': comment,
            'date': datetime.datetime.now(),
        })

        database.users.update_one({'steam_id': user_id}, {'$set': user})
        trade = user_trades[0]
        if trade['initiator_id'] == steam_id:
            trade['initiator_rated'] = True
        else:
            trade['user_rated'] = True
        database.trades.update_one({'_id': trade['_id']}, {'$set': trade})
        return 'OK'
    except Exception as e:
        print(e)
        return Response('Failed to rate user', status=500)

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

@app.route('/messages')
def messages():
    steam_id = request.cookies.get('steam_id')
    if steam_id is None:
        return redirect('/')

    user_id = request.args.get('user_id')
    if user_id is not None:
        message = {
            'from': steam_id,
            'to': user_id,
            'timestamp': datetime.datetime.now(),
            'hidden': True,
        }
        database.messages.insert_one(message)

        delete_thread = threading.Timer(10, database.messages.delete_one, [message])
        delete_thread.start()

        return redirect('/messages')

    user_data = database.users.find_one({'steam_id': steam_id})
    return render_template(
        'messages.html',
        username=user_data['username'],
        avatar=user_data['avatar'],
    )

@app.route('/send_message', methods=['POST'])
def send_message():
    # response = make_response()
    # response.set_cookie('steam_id', '76561199380456538', secure=True)
    # return response

    try:
        steam_id = request.cookies.get('steam_id')

        message = request.get_json()
        message = {
            'from': steam_id,
            'to': request.json['user_id'],
            'content': request.json['content'],
            'timestamp': datetime.datetime.now(),
        }
        print(message)

        database.messages.insert_one(message)
    except Exception as e:
        # Internal server error
        return Response('Failed to send the message', status=500)
    return 'OK'

@app.route('/get_contacts')
def get_contacts():
    steam_id = request.cookies.get('steam_id')

    if steam_id is None:
        return Response('Not logged in', status=401)

    contacts = database.messages.aggregate([
        {
            '$match': {
                '$or': [
                    {
                        'from': steam_id
                    }, {
                        'to': steam_id
                    }
                ]
            }
        }, {
            '$group': {
                '_id': {
                    'from': '$from', 
                    'to': '$to'
                }, 
                'timestamp': {
                    '$last': '$timestamp'
                }
            }
        }, {
            '$project': {
                '_id': 0, 
                'timestamp': 1, 
                'steam_id': {
                    '$cond': {
                        'if': {
                            '$eq': [
                                '$_id.from', steam_id
                            ]
                        }, 
                        'then': '$_id.to', 
                        'else': '$_id.from'
                    }
                }
            }
        }, {
            '$lookup': {
                'from': 'users', 
                'localField': 'steam_id', 
                'foreignField': 'steam_id', 
                'as': 'user_info'
            }
        }, {
            '$unwind': '$user_info'
        }, {
            '$sort': {
                'timestamp': -1
            }
        }, {
            '$group': {
                '_id': '$steam_id', 
                'latestTimestamp': {
                    '$first': '$timestamp'
                }, 
                'username': {
                    '$first': '$user_info.username'
                }, 
                'avatar': {
                    '$first': '$user_info.avatar'
                }
            }
        }, {
            '$project': {
                '_id': 0, 
                'steam_id': '$_id', 
                'username': 1, 
                'avatar': 1, 
                'timestamp': '$latestTimestamp'
            }
        }
    ])
    contacts = list(contacts)
    contacts = sorted(contacts, key=lambda c: c['timestamp'], reverse=True)
    # contact_ids = [c['steam_id'] for c in contacts]

    # contacts = database.users.find({'steam_id': {'$in': contact_ids}}, {'_id': 0, 'steam_id': 1, 'username': 1, 'avatar': 1})

    return jsonify({
        'contacts': contacts,
    })


@app.route('/get_messages')
def get_messages():
    steam_id = request.cookies.get('steam_id')

    if steam_id is None:
        return Response('Not logged in', status=401)

    user_id = request.args.get('user_id')

    messages = database.messages.find(
        {'$or': [
            {'from': steam_id, 'to': user_id},
            {'from': user_id, 'to': steam_id},
        ]},
        {'_id': 0}
    )

    messages = sorted(list(messages), key=lambda m: m['timestamp'])
    for message in messages:
        # show how long ago the message was sent (e.g. 5 minutes ago, 1 hour ago, 1 day ago)
        message['timestamp'] = format_time_ago(message['timestamp'])

    trades = database.trades.aggregate([
            {
                '$match': {
                    '$or': [
                        {
                            'initiator_id': steam_id, 
                            'user_id': user_id, 
                            'completed': False,
                            'cancelled': False
                        }, {
                            'initiator_id': user_id, 
                            'user_id': steam_id, 
                            'completed': False,
                            'cancelled': False
                        }
                    ]
                }
            }, {
                '$project': {
                    'initiator_rated': 0, 
                    'user_rated': 0, 
                    'user_id': 0, 
                    'completed': 0
                }
            }
        ])

    trades = list(trades)
    for trade in trades:
        trade['_id'] = str(trade['_id'])

    return jsonify({
        'messages': messages,
        'trades': trades,
    })

@app.route('/change_trade_status', methods=['POST'])
def change_trade_status():
    try:
        steam_id = request.cookies.get('steam_id')

        if steam_id is None:
            return Response('Not logged in', status=401)

        trade_id = request.json['trade_id']
        command = request.json['command']

        trade = database.trades.find_one({'_id': ObjectId(trade_id)})

        if command == 'accept':
            trade['accepted'] = True
        elif command == 'cancel':
            trade['cancelled'] = True
        elif command == 'complete':
            if trade['initiator_id'] == steam_id:
                trade['initiator_completed'] = True
            else:
                trade['user_completed'] = True
            if trade['initiator_completed'] and trade['user_completed']:
                trade['completed'] = True

        database.trades.update_one({'_id': ObjectId(trade_id)}, {'$set': trade})
        return 'OK'
    except Exception as e:
        return Response('Failed to change trade status', status=500)

@app.route('/trade')
def trade():
    steam_id = request.cookies.get('steam_id')

    if steam_id is None:
        return redirect('/')

    user_id = request.args.get('user_id')

    if user_id is None:
        return make_response('User not specified', 400)

    tradeData = {
        'initiator_id': steam_id,
        'user_id': user_id,
        'timestamp': datetime.datetime.now(),
        'accepted': False,
        'initiator_rated': False,
        'user_rated': False,
        'initiator_completed': False,
        'user_completed': False,
        'completed': False,
        'cancelled': False,
    }

    try:
        database.trades.insert_one(tradeData)
    except Exception as e:
        return make_response('Failed to create trade', 500)

    return make_response('OK', 200)
    

if __name__ == '__main__':
    app.run(port=80, host="127.0.0.1", debug=True) 


