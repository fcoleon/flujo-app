from flask import jsonify


def respond(data,status=200): return jsonify(data),status
