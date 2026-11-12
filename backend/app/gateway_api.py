from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from .gateway import route_dict
from .models import GatewayRoute
from .schemas import RouteCreate, RoutePatch
from .settings_store import update_env_file

router = APIRouter(prefix="/api/gateway")


@router.get("/routes")
def list_routes(request: Request):
    with request.app.state.session_factory() as session:
        routes = session.scalars(
            select(GatewayRoute).order_by(
                GatewayRoute.priority, GatewayRoute.created_at, GatewayRoute.id
            )
        ).all()
        return {"items": [route_dict(route, request.app) for route in routes]}


@router.post("/routes", status_code=201)
def create_route(payload: RouteCreate, request: Request):
    values = payload.model_dump(exclude={"api_key"})
    route_id = str(uuid4())
    key_name = f"ROUTE_KEY_{uuid4().hex.upper()}" if payload.api_key else None
    route = GatewayRoute(id=route_id, key_name=key_name, **values)
    if key_name:
        update_env_file(request.app.state.env_path, {key_name: payload.api_key})
    with request.app.state.session_factory() as session:
        session.add(route)
        session.commit()
        return route_dict(route, request.app)


@router.patch("/routes/{route_id}")
def update_route(route_id: str, payload: RoutePatch, request: Request):
    with request.app.state.session_factory() as session:
        route = session.get(GatewayRoute, route_id)
        if route is None:
            raise HTTPException(status_code=404, detail="路由规则不存在")
        values = payload.model_dump(exclude_unset=True)
        secret_supplied = "api_key" in values
        api_key = values.pop("api_key", None)
        if secret_supplied and api_key is not None:
            if api_key and not route.key_name:
                route.key_name = f"ROUTE_KEY_{uuid4().hex.upper()}"
            if route.key_name:
                update_env_file(request.app.state.env_path, {route.key_name: api_key})
        for field, value in values.items():
            setattr(route, field, value)
        session.commit()
        return route_dict(route, request.app)


@router.delete("/routes/{route_id}", status_code=204)
def delete_route(route_id: str, request: Request):
    with request.app.state.session_factory() as session:
        route = session.get(GatewayRoute, route_id)
        if route is None:
            raise HTTPException(status_code=404, detail="路由规则不存在")
        if route.key_name:
            update_env_file(request.app.state.env_path, {route.key_name: ""})
        session.delete(route)
        session.commit()
        request.app.state.circuits.reset(route_id)
    return Response(status_code=204)


@router.post("/routes/{route_id}/reset")
def reset_route_circuit(route_id: str, request: Request):
    with request.app.state.session_factory() as session:
        route = session.get(GatewayRoute, route_id)
        if route is None:
            raise HTTPException(status_code=404, detail="路由规则不存在")
        request.app.state.circuits.reset(route_id)
        return route_dict(route, request.app)
