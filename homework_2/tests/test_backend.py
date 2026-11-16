"""Role 4 API tests; run from homework_2 with pytest -q tests/test_backend.py."""
from fastapi.testclient import TestClient
from app.main import create_app


def test_users_filters_isolation_restart_and_posts(tmp_path):
    path = str(tmp_path / "news.db")
    with TestClient(create_app(path, polling=False)) as client:
        u1 = client.post('/api/users', json={'name':'Аня'}).json()['id']
        u2 = client.post('/api/users', json={'name':'Борис'}).json()['id']
        assert client.post('/api/users', json={'name':'Аня'}).status_code == 409
        f1 = client.post(f'/api/users/{u1}/filters',json={
            'name':'Нефть', 'kind':'keyword', 'params':{'any':['нефть']}})
        assert f1.status_code == 201, f1.text
        f2 = client.post(f'/api/users/{u2}/filters',json={
            'name':'Банки', 'kind':'keyword', 'params':{'any':['банк']}})
        assert f2.status_code == 201, f2.text
        ch = client.post('/api/channels',json={'title':'Тест','user_id':u1})
        assert ch.status_code==201,ch.text
        channel = ch.json()
        assert '/rss/' in channel['rss_url']
        assert client.get(f"/rss/{channel['id']}.xml").status_code==200
        assert client.post(f'/api/users/{u2}/feeds',json={'feed_id':channel['feed_id']}).status_code==201
        posted = client.post(f"/api/channels/{channel['id']}/articles",json={
            'title':'Нефть выросла', 'text':'Котировки нефти выросли'})
        assert posted.status_code==201,posted.text
        assert posted.json()['new_articles']==1
        news1=client.get(f'/api/users/{u1}/news').json()
        assert len(news1)==1
        assert news1[0]['matches'][0]['filter_name']=='Нефть'
        assert 'details' in news1[0]['matches'][0]
        assert client.get(f'/api/users/{u2}/news').json()==[]
        filt_id=f2.json()['id']
        updated=client.put(f'/api/users/{u2}/filters/{filt_id}',json={
            'name':'Energy','kind':'keyword','params':{'any':['нефть']}})
        assert updated.status_code==200
        assert len(client.get(f'/api/users/{u2}/news').json())==1
        assert client.get(f'/api/users/{u1}/filters').json()[0]['name']=='Нефть'

    with TestClient(create_app(path,polling=False)) as restarted:
        assert len(restarted.get('/api/users').json())==2
        assert len(restarted.get(f'/api/users/{u1}/feeds').json())==1
        assert len(restarted.get(f'/api/users/{u1}/news').json())==1
        assert len(restarted.get(f'/api/users/{u2}/news').json())==1
        assert 'Нефть' in restarted.get(f"/rss/{channel['id']}.xml").text


def test_validation_and_subscription(tmp_path):
    with TestClient(create_app(str(tmp_path/'test.db'),polling=False)) as c:
        u = c.post('/api/users',json={'name':'Test'}).json()['id']
        assert c.get('/api/users/999/filters').status_code==404
        assert c.post(f'/api/users/{u}/filters',json={
            'name':'empty','kind':'keyword','params':{}}).status_code==422
        assert c.post(f'/api/users/{u}/filters',json={
            'name':'bad','kind':'topic','params':{'description':'science','threshold':2}}).status_code==422
        assert c.post(f'/api/users/{u}/feeds',json={}).status_code==422
        ch = c.post('/api/channels',json={'title':'Sample','user_id':u}).json()
        assert c.delete(f"/api/users/{u}/feeds/{ch['feed_id']}").status_code==200
        assert c.get(f'/api/users/{u}/feeds').json()==[]
        assert c.get('/rss/999.xml').status_code==404
