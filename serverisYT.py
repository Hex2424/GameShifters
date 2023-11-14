import os
from flask import Flask, request, make_response, jsonify, render_template
from decouple import config
from steam import Steam
from pysteamsignin.steamsignin import SteamSignIn

app = Flask(__name__, template_folder='html', static_folder='css')

# KEY = config("STEAM_API_KEY")
KEY = ""
steam = Steam(KEY)

@app.route('/')
def main():
    steamID = request.cookies.get('steam_id')
    if not steamID:
        return render_template('index.html')

    user = steam.users.get_user_details(steamID)['player']
    owned_games = steam.users.get_owned_games(steamID)['games']
    steam_level = steam.users.get_user_steam_level(steamID)
    badges = steam.users.get_user_badges(steamID)

    return render_template(
        'main.html',
        username=user['personaname'],
        avatar=user['avatarfull'],
        steam_level=steam_level['player_level'],
        badges=badges,
        owned_games=owned_games
    )

@app.route('/login')
def login():
    steamLogin = SteamSignIn()
    return steamLogin.RedirectUser(steamLogin.ConstructURL('http://localhost:8080/processlogin'))

@app.route('/logout')
def logout():
    response = make_response('Logged out successfully!')
    response.set_cookie('steam_id', '', expires=0)
    return response

@app.route('/processlogin')
def process():
    returnData = request.values
    steamLogin = SteamSignIn()
    steamID = steamLogin.ValidateResults(returnData)

    if steamID is not False:
        response = make_response('Logged in successfully!')
        response.set_cookie('steam_id', steamID)
        response.headers['Location'] = '/'
        return response
    else:
        return 'Failed to log in, bad details?'

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

if __name__ == '__main__':
    app.run(port=80, host="127.0.0.1", debug=True) 

