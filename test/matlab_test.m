clear all; 
close all;

rand('twister', 31337)
n = 1000;
n_test = 100;
D = 5;
E = 3;

f = @(x) exp(-500*sum(0.0001*x.^2,2));
%m0 = randn(1,D)'
%S0 = randn(D,D)'
%S0 = eye(D);

X = 10*(rand(D,n)' - 0.5);
Y = zeros(n,E);
for i=1:E
    Y(:,i) = i*f(X) + 0.01*(rand(1,n) - 0.5)';
end


model.fcn = @gp0d;                % function for GP predictions
model.train = @train;             % function to train dynamics model
trainOpt = [300 500];                % defines the max. number of line searches

model.inputs  = X;
model.targets = Y;
model = model.train(model, [], trainOpt);  %  train dynamics GP
model.hyp

Xtest = 10*(rand(D,n_test)' -0.5);
Ytest = zeros(E,n_test)';
for i=1:E
    Ytest(:,i) = i*f(Xtest); %+ 0.25*randn((n,1)) + 0.125
end

for i=1:size(Ytest,1)
    disp(['x: ', num2str(Xtest(i,:)),', y: ',num2str(Ytest(i,:))])
    [M, S, V] = model.fcn(model, Xtest(i,:)', 0.01*ones(D));
    M
    S
    V
    disp('---')
end
