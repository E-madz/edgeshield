from django.contrib.auth import get_user_model, password_validation
from django.db import IntegrityError
from rest_framework import serializers

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})

    class Meta:
        model = User
        fields = ('id', 'email', 'password', 'first_name', 'last_name')
        read_only_fields = ('id',)

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def validate_password(self, value):
        # Runs the AUTH_PASSWORD_VALIDATORS from settings (length, common, numeric...)
        password_validation.validate_password(value)
        return value

    def create(self, validated_data):
        try:
            return User.objects.create_user(**validated_data)
        except IntegrityError:
            # Two simultaneous requests can both pass validate_email; the UNIQUE
            # index then rejects the loser. Turn that into a clean 400, not a 500.
            raise serializers.ValidationError(
                {'email': ['A user with this email already exists.']}
            ) from None


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'email', 'first_name', 'last_name', 'date_joined')
        read_only_fields = fields
