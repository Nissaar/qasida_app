from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from .models import Qasida, Tag, Suggestion
from django.contrib import messages
from django.core.paginator import Paginator

def home(request):
    recent_qasidas = Qasida.objects.order_by('-created_at')[:10]
    return render(request, 'core/home.html', {'qasidas': recent_qasidas})

def search(request):
    query = request.GET.get('q', '')
    lang_filter = request.GET.get('lang', '')
    tag_filter = request.GET.get('tag', '')

    qasidas = Qasida.objects.all()

    if query:
        qasidas = qasidas.filter(
            Q(lyrics__icontains=query) |
            Q(title__icontains=query) |
            Q(author__icontains=query)
        )
    if lang_filter:
        qasidas = qasidas.filter(language__icontains=lang_filter)
    if tag_filter:
        qasidas = qasidas.filter(tags__name__icontains=tag_filter)
        
    qasidas = qasidas.distinct().order_by('-created_at')
    
    paginator = Paginator(qasidas, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'query': query,
        'lang_filter': lang_filter,
        'tag_filter': tag_filter,
    }
    return render(request, 'core/search.html', context)

def qasida_detail(request, pk):
    qasida = get_object_or_404(Qasida, pk=pk)
    
    if request.method == 'POST':
        email = request.POST.get('email')
        suggested_lyrics = request.POST.get('suggested_lyrics')
        suggested_tags = request.POST.get('suggested_tags')
        
        if email:
            Suggestion.objects.create(
                qasida=qasida,
                email=email,
                suggested_lyrics=suggested_lyrics,
                suggested_tags=suggested_tags
            )
            messages.success(request, 'Your suggestion has been submitted for review.')
            return redirect('qasida_detail', pk=pk)
        else:
            messages.error(request, 'Email is required to submit a suggestion.')
            
    return render(request, 'core/detail.html', {'qasida': qasida})
