# kubectl basics

```
kubectl <action, do somethig [apply, delete, get, etc]> <object type> <object id or name> `  
```

## Cluster info 
```
kubectl cluster-info

- Example output
Kubernetes control plane is running at https://kubernetes.docker.internal:6443
CoreDNS is running at https://kubernetes.docker.internal:6443/api/v1/namespaces/kube-system/services/kube-dns:dns/proxy

To further debug and diagnose cluster problems, use 'kubectl cluster-info dump'.
```

## List nodes
```
kubectl get nodes

- Example output
NAME             STATUS   ROLES           AGE   VERSION
docker-desktop   Ready    control-plane   49d   v1.34.1
```
## Create a pod 
```bash
kubectl run firstpod --image=nginx:1.14 --restart=Never
```

## Connect to a pod 
```bash
kubectl exec -it firstpod -- bash

exit
```

## Describe a pod 
```bash
kubectl describe pod firstpod
```

## Delete pod
```bash
kubectl delete pod firstpod
```

## Create a deployment

```bash
kubectl create deployment hello-k8s --image=nginx:1.14
```

## Exposing a service as a NodePort

```bash
kubectl expose deployment hello-k8s --type=NodePort --port=80
```

or 

```bash
kubectl expose deployment.apps/hello-k8s --type=NodePort --port=80
```

- See all 

```bash
kubectl get all

NAME                            READY   STATUS    RESTARTS   AGE
pod/hello-k8s-7b9fdcffc-k99gr   1/1     Running   0          54s

NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)        AGE
service/hello-k8s    NodePort    10.104.39.122   <none>        80:32536/TCP   44s
service/kubernetes   ClusterIP   10.96.0.1       <none>        443/TCP        49d

NAME                        READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/hello-k8s   1/1     1            1           54s

NAME                                  DESIRED   CURRENT   READY   AGE
replicaset.apps/hello-k8s-7b9fdcffc   1         1         1       54s
```

## see the app
- Format  
` curl http://<cluster dns or ip>:<service-port> `  

See the page
```
 curl http://kubernetes.docker.internal:32536
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
<style>
    body {
        width: 35em;
        margin: 0 auto;
        font-family: Tahoma, Verdana, Arial, sans-serif;
    }
</style>
</head>
<body>
<h1>Welcome to nginx!</h1>
<p>If you see this page, the nginx web server is successfully installed and
working. Further configuration is required.</p>

<p>For online documentation and support please refer to
<a href="http://nginx.org/">nginx.org</a>.<br/>
Commercial support is available at
<a href="http://nginx.com/">nginx.com</a>.</p>

<p><em>Thank you for using nginx.</em></p>
</body>
</html>
```

## cleanup
```bash
kubectl delete deployment hello-k8s
kubectl delete service hello-k8s
``` 
- Check
```bash
kubectl get all

- Expected output
NAME                 TYPE        CLUSTER-IP   EXTERNAL-IP   PORT(S)   AGE
service/kubernetes   ClusterIP   10.96.0.1    <none>        443/TCP   26m
```